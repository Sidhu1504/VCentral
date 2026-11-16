package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func signed(t *testing.T, priv ed25519.PrivateKey, p taskPayload) SignedTask {
	t.Helper()
	data, _ := json.Marshal(p)
	return SignedTask{Payload: string(data), Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(priv, data))}
}

func testRunner(t *testing.T) (*TaskRunner, *Config, ed25519.PrivateKey) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	cfg := &Config{AgentID: "agent-1", TaskKey: base64.StdEncoding.EncodeToString(pub)}
	r := newTaskRunner(nil, cfg, filepath.Join(t.TempDir(), "agent.json"))
	return r, cfg, priv
}

func payload(id int64) taskPayload {
	return taskPayload{V: 1, RunID: id, AgentID: "agent-1", Kind: "script", Shell: "bash", Script: "echo hi",
		Timeout: 30, ExpiresAt: time.Now().Add(time.Hour).UTC().Format(time.RFC3339)}
}

func TestVerify(t *testing.T) {
	r, cfg, priv := testRunner(t)

	p, err := r.verify(cfg, signed(t, priv, payload(1)))
	if err != nil || p.Script != "echo hi" {
		t.Fatalf("valid task rejected: %v", err)
	}

	tampered := signed(t, priv, payload(2))
	tampered.Payload = strings.Replace(tampered.Payload, "echo hi", "rm -rf /", 1)
	if _, err := r.verify(cfg, tampered); err == nil || err.Error() != "bad signature" {
		t.Fatalf("tampered task accepted: %v", err)
	}

	_, otherKey, _ := ed25519.GenerateKey(rand.Reader)
	if _, err := r.verify(cfg, signed(t, otherKey, payload(3))); err == nil {
		t.Fatal("task signed with another key accepted")
	}

	wrongAgent := payload(4)
	wrongAgent.AgentID = "agent-2"
	if _, err := r.verify(cfg, signed(t, priv, wrongAgent)); err == nil {
		t.Fatal("task for another agent accepted")
	}

	expired := payload(5)
	expired.ExpiresAt = time.Now().Add(-time.Hour).UTC().Format(time.RFC3339)
	if p, err := r.verify(cfg, signed(t, priv, expired)); err == nil || p == nil {
		t.Fatal("expired task accepted (or not reported back)")
	}

	if _, err := r.verify(&Config{AgentID: "agent-1"}, signed(t, priv, payload(6))); err == nil {
		t.Fatal("task accepted without a trusted key")
	}
}

func TestReplayIsRefused(t *testing.T) {
	r, _, _ := testRunner(t)
	if !r.markExecuted(7) {
		t.Fatal("first run refused")
	}
	if r.markExecuted(7) {
		t.Fatal("replayed run accepted")
	}
	// The record survives a restart.
	r2 := newTaskRunner(nil, r.cfg, r.cfgPath)
	if r2.markExecuted(7) {
		t.Fatal("replay accepted after restart")
	}
}

func TestOutputLimit(t *testing.T) {
	b := &outputBuffer{}
	chunk := []byte(strings.Repeat("x", 4096))
	for i := 0; i < (outputLimit/4096)+10; i++ {
		b.Write(chunk)
	}
	if got := len(b.take()); got != outputLimit || !b.truncated {
		t.Fatalf("kept %d bytes, truncated=%v", got, b.truncated)
	}
}

func TestTemplateMode(t *testing.T) {
	cfg := &Config{Server: "https://v", EnrollToken: "vct_x", Template: true, BiosUUID: "4213-aaaa"}
	if cfg.enrolled() {
		t.Fatal("template config must not count as enrolled")
	}
	if !isTemplateItself(cfg, "4213-aaaa") {
		t.Fatal("the template itself must stay idle")
	}
	if isTemplateItself(cfg, "4213-bbbb") {
		t.Fatal("a VM deployed from the template must enroll")
	}
	path := filepath.Join(t.TempDir(), "agent.json")
	if err := saveConfig(path, cfg); err != nil {
		t.Fatal(err)
	}
	if _, err := loadConfig(path); err != nil {
		t.Fatalf("template config (token, no secret) must load: %v", err)
	}
	if err := saveConfig(path, &Config{Server: "https://v"}); err != nil {
		t.Fatal(err)
	}
	if _, err := loadConfig(path); err == nil {
		t.Fatal("config without secret or token must be rejected")
	}
}
