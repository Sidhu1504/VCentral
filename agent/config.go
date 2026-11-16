package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Config is written once at enrollment and read by the service.
type Config struct {
	Server      string `json:"server"`
	Fingerprint string `json:"fingerprint,omitempty"` // SHA-256 of the server certificate (pinned when set)
	AgentID     string `json:"agent_id"`
	Secret      string `json:"secret"`
	BiosUUID    string `json:"bios_uuid,omitempty"` // identity at enrollment; a change means this disk was cloned
	// Kept only when the enrollment token is reusable, so a cloned VM can enroll itself as a new agent.
	EnrollToken string `json:"enroll_token,omitempty"`
	// Public key (Ed25519, base64) that tasks must be signed with. Learned from the server once; never changed.
	TaskKey string `json:"task_key,omitempty"`
	// Set with "install --disable-tasks": this machine only reports, it never runs tasks.
	TasksDisabled bool `json:"tasks_disabled,omitempty"`
	// Set with "install --template": installed in a VM template. The template itself stays idle; every VM
	// deployed from it (new BIOS UUID) enrolls itself with EnrollToken on first boot.
	Template bool `json:"template,omitempty"`
}

// enrolled reports whether this machine already has its own agent identity.
func (c *Config) enrolled() bool { return c.Secret != "" && c.AgentID != "" }

func loadConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("invalid config %s: %w", path, err)
	}
	if cfg.Server == "" || (cfg.Secret == "" && cfg.EnrollToken == "") {
		return nil, errors.New("config is missing server or credentials; re-run install")
	}
	return &cfg, nil
}

func saveConfig(path string, cfg *Config) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	return restrictPermissions(dir)
}

// normalizeFingerprint accepts "sha256:AA:BB...", "AABB..." etc. and returns lowercase hex.
func normalizeFingerprint(fp string) string {
	fp = strings.ToLower(strings.TrimSpace(fp))
	fp = strings.TrimPrefix(fp, "sha256:")
	fp = strings.NewReplacer(":", "", " ", "").Replace(fp)
	return fp
}
