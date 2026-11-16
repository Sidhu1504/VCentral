package main

import (
	"bytes"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

type apiError struct {
	Status int
	Detail string
}

func (e *apiError) Error() string { return fmt.Sprintf("server returned %d: %s", e.Status, e.Detail) }

type Client struct {
	server string
	secret string
	http   *http.Client
}

func newClient(server, fingerprint, secret string) (*Client, error) {
	server = strings.TrimRight(strings.TrimSpace(server), "/")
	u, err := url.Parse(server)
	if err != nil || u.Host == "" {
		return nil, fmt.Errorf("invalid server URL %q", server)
	}
	if u.Scheme != "https" && !(u.Scheme == "http" && os.Getenv("VCENTRAL_AGENT_ALLOW_HTTP") == "1") {
		return nil, fmt.Errorf("server URL must start with https://")
	}
	transport := &http.Transport{
		Proxy:               http.ProxyFromEnvironment,
		TLSClientConfig:     tlsConfig(normalizeFingerprint(fingerprint)),
		TLSHandshakeTimeout: 15 * time.Second,
		IdleConnTimeout:     90 * time.Second,
		MaxIdleConns:        2,
	}
	return &Client{server: server, secret: secret, http: &http.Client{Transport: transport, Timeout: 60 * time.Second}}, nil
}

// tlsConfig trusts the system CAs, or — when a fingerprint is given — only that exact certificate.
// Pinning lets vCentral use a self-signed certificate without exposing agents to impersonation.
func tlsConfig(fingerprint string) *tls.Config {
	cfg := &tls.Config{MinVersion: tls.VersionTLS12}
	if fingerprint == "" {
		return cfg
	}
	cfg.InsecureSkipVerify = true // replaced by the exact-certificate check below
	cfg.VerifyConnection = func(cs tls.ConnectionState) error {
		if len(cs.PeerCertificates) == 0 {
			return errors.New("server sent no certificate")
		}
		sum := sha256.Sum256(cs.PeerCertificates[0].Raw)
		if got := hex.EncodeToString(sum[:]); got != fingerprint {
			return fmt.Errorf("server certificate (sha256 %s…) does not match the pinned fingerprint; "+
				"if the vCentral certificate was replaced, re-run install with the new fingerprint", got[:16])
		}
		return nil
	}
	return cfg
}

func (c *Client) do(method, path string, body interface{}, out interface{}) error {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(data)
	}
	req, err := http.NewRequest(method, c.server+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "vcentral-agent/"+Version)
	if c.secret != "" {
		req.Header.Set("Authorization", "Bearer "+c.secret)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode >= 300 {
		var e struct {
			Detail interface{} `json:"detail"`
		}
		detail := strings.TrimSpace(string(data))
		if json.Unmarshal(data, &e) == nil && e.Detail != nil {
			detail = fmt.Sprint(e.Detail)
		}
		if len(detail) > 300 {
			detail = detail[:300]
		}
		return &apiError{Status: resp.StatusCode, Detail: detail}
	}
	if out != nil {
		return json.Unmarshal(data, out)
	}
	return nil
}

type EnrollRequest struct {
	Token string `json:"token"`
	Facts Facts  `json:"facts"`
}

type EnrollResponse struct {
	AgentID        string `json:"agent_id"`
	Secret         string `json:"secret"`
	ReusableToken  bool   `json:"reusable_token"`
	ReportInterval int    `json:"report_interval"`
	TaskPublicKey  string `json:"task_public_key"`
}

type ReportResponse struct {
	ReportInterval    int          `json:"report_interval"`
	InventoryInterval int          `json:"inventory_interval"`
	SendInventory     bool         `json:"send_inventory"`
	LatestVersion     string       `json:"latest_version"`
	TaskPublicKey     string       `json:"task_public_key"`
	Tasks             []SignedTask `json:"tasks"`
	Cancel            []int64      `json:"cancel"`
}

type SignedTask struct {
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}

type TaskResult struct {
	ExitCode  *int   `json:"exit_code"`
	Output    string `json:"output"`
	Error     string `json:"error,omitempty"`
	TimedOut  bool   `json:"timed_out"`
	Cancelled bool   `json:"cancelled"`
}

// TaskProgress sends output produced so far; the server answers whether the run was cancelled.
func (c *Client) TaskProgress(runID int64, output string) (bool, error) {
	var out struct {
		Cancel bool `json:"cancel"`
	}
	err := c.do("POST", fmt.Sprintf("/api/agent/tasks/%d/progress", runID), map[string]string{"output": output}, &out)
	return out.Cancel, err
}

func (c *Client) TaskResult(runID int64, res TaskResult) error {
	return c.do("POST", fmt.Sprintf("/api/agent/tasks/%d/result", runID), res, nil)
}

// Download fetches a file from the server over the same pinned, authenticated connection.
func (c *Client) Download(path string, limit int64) ([]byte, error) {
	req, err := http.NewRequest("GET", c.server+path, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "vcentral-agent/"+Version)
	req.Header.Set("Authorization", "Bearer "+c.secret)
	client := *c.http
	client.Timeout = 10 * time.Minute
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, &apiError{Status: resp.StatusCode, Detail: "download failed"}
	}
	return io.ReadAll(io.LimitReader(resp.Body, limit))
}

func (c *Client) Enroll(req EnrollRequest) (*EnrollResponse, error) {
	var out EnrollResponse
	if err := c.do("POST", "/api/agent/enroll", req, &out); err != nil {
		return nil, err
	}
	if out.AgentID == "" || out.Secret == "" {
		return nil, errors.New("server returned an incomplete enrollment response")
	}
	return &out, nil
}

func (c *Client) Report(rep *Report) (*ReportResponse, error) {
	var out ReportResponse
	if err := c.do("POST", "/api/agent/report", rep, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// CheckTemplateToken asks vCentral whether a token is valid and reusable, without using it up.
func (c *Client) CheckTemplateToken(token string) error {
	var out struct {
		Valid    bool   `json:"valid"`
		Reusable bool   `json:"reusable"`
		Reason   string `json:"reason"`
	}
	if err := c.do("POST", "/api/agent/token-check", map[string]string{"token": token}, &out); err != nil {
		return fmt.Errorf("could not check the token with %s: %w", c.server, err)
	}
	if !out.Valid {
		return fmt.Errorf("enrollment token rejected: %s", out.Reason)
	}
	if !out.Reusable {
		return errors.New("template mode needs a reusable token (unlimited or many uses); create one marked “For a VM template”")
	}
	return nil
}

func (c *Client) Ping() error {
	return c.do("GET", "/api/health", nil, nil)
}
