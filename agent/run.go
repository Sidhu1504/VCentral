package main

import (
	"errors"
	"fmt"
	"log"
	"math/rand"
	"os"
	"time"
)

type Report struct {
	AgentVersion string       `json:"agent_version"`
	Platform     string       `json:"platform"`
	BiosUUID     string       `json:"bios_uuid"`
	IPs          []string     `json:"ips"` // current addresses on connected, non-virtual interfaces
	Metrics      Metrics      `json:"metrics"`
	TopProcesses TopProcesses `json:"top_processes"`
	Inventory    *Inventory   `json:"inventory,omitempty"`
}

func clampSeconds(v, lo, hi int) time.Duration {
	if v < lo {
		v = lo
	}
	if v > hi {
		v = hi
	}
	return time.Duration(v) * time.Second
}

// enroll registers this machine with the server using a one-time or reusable token.
func enroll(server, fingerprint, token string) (*Config, error) {
	c, err := newClient(server, fingerprint, "")
	if err != nil {
		return nil, err
	}
	facts := collectFacts()
	resp, err := c.Enroll(EnrollRequest{Token: token, Facts: facts})
	if err != nil {
		var ae *apiError
		if errors.As(err, &ae) && (ae.Status == 401 || ae.Status == 403) {
			return nil, fmt.Errorf("enrollment token rejected: %s", ae.Detail)
		}
		return nil, fmt.Errorf("could not enroll with %s: %w", server, err)
	}
	cfg := &Config{Server: c.server, Fingerprint: normalizeFingerprint(fingerprint), AgentID: resp.AgentID,
		Secret: resp.Secret, BiosUUID: facts.BiosUUID, TaskKey: resp.TaskPublicKey}
	if resp.ReusableToken {
		cfg.EnrollToken = token
	}
	return cfg, nil
}

// reenroll handles a cloned disk: the copied credentials belong to the original machine.
func reenroll(cfgPath string, cfg *Config) (*Config, error) {
	if cfg.EnrollToken == "" {
		return nil, errors.New("this machine looks like a clone of another agent; re-run install with a new enrollment token")
	}
	fresh, err := enroll(cfg.Server, cfg.Fingerprint, cfg.EnrollToken)
	if err != nil {
		return nil, err
	}
	fresh.TasksDisabled = cfg.TasksDisabled
	if err := saveConfig(cfgPath, fresh); err != nil {
		return nil, err
	}
	log.Printf("re-enrolled as new agent %s", fresh.AgentID)
	return fresh, nil
}

// waitAndEnroll handles an agent installed with --template. On the template itself (same BIOS UUID as at install)
// it stays idle. On a VM deployed from the template it enrolls with the stored token, retrying until vCentral
// answers. Returns nil config only if stopped.
// isTemplateItself: installed with --template and still on the same machine (vSphere gives clones a new BIOS UUID).
func isTemplateItself(cfg *Config, biosUUID string) bool {
	return cfg.Template && biosUUID != "" && biosUUID == cfg.BiosUUID
}

func waitAndEnroll(cfgPath string, cfg *Config, biosUUID string, stop <-chan struct{}) (*Config, error) {
	if isTemplateItself(cfg, biosUUID) {
		log.Printf("template mode: this is the template itself, so the agent stays idle; VMs deployed from it enroll themselves")
		<-stop
		return nil, nil
	}
	wait := time.Duration(5+rand.Intn(20)) * time.Second // spread many VMs deployed at once
	for attempt := 1; ; attempt++ {
		select {
		case <-stop:
			return nil, nil
		case <-time.After(wait):
		}
		fresh, err := enroll(cfg.Server, cfg.Fingerprint, cfg.EnrollToken)
		if err == nil {
			fresh.TasksDisabled = cfg.TasksDisabled
			if err := saveConfig(cfgPath, fresh); err != nil {
				return nil, err
			}
			log.Printf("enrolled automatically as %s", fresh.AgentID)
			return fresh, nil
		}
		if attempt == 1 || attempt%10 == 0 {
			log.Printf("automatic enrollment failed (attempt %d): %v", attempt, err)
		}
		if wait < 5*time.Minute {
			wait *= 2
		}
	}
}

func runLoop(cfgPath string, stop <-chan struct{}) error {
	cfg, err := loadConfig(cfgPath)
	if err != nil {
		return err
	}
	biosUUID, _ := biosIdentity()
	if !cfg.enrolled() {
		// Template mode: idle on the template itself, enroll on every VM deployed from it.
		fresh, err := waitAndEnroll(cfgPath, cfg, biosUUID, stop)
		if fresh == nil {
			return err // stopped while waiting
		}
		cfg = fresh
	} else if cfg.BiosUUID != "" && biosUUID != "" && cfg.BiosUUID != biosUUID {
		log.Printf("machine identity changed since enrollment (cloned VM?)")
		if fresh, err := reenroll(cfgPath, cfg); err != nil {
			log.Printf("%v", err)
		} else {
			cfg = fresh
		}
	}
	client, err := newClient(cfg.Server, cfg.Fingerprint, cfg.Secret)
	if err != nil {
		return err
	}
	log.Printf("vCentral agent %s (%s) started; reporting to %s as %s", Version, Platform, cfg.Server, cfg.AgentID)
	if self, err := os.Executable(); err == nil {
		cleanupOldExecutable(self) // leftover from a previous update on Windows
	}
	runner := newTaskRunner(client, cfg, cfgPath)

	col := NewCollector()
	interval, invInterval := 20*time.Second, time.Hour
	var lastInventory time.Time
	sendInventory := true
	failures := 0
	wait := time.Duration(2+rand.Intn(10)) * time.Second // spread a fleet-wide restart

	for {
		select {
		case <-stop:
			return nil
		case <-runner.restart:
			// Exit with an error so systemd / the Windows service recovery starts the new binary.
			return errRestart
		case <-time.After(wait):
		}
		rep := &Report{AgentVersion: Version, Platform: Platform, BiosUUID: biosUUID, IPs: primaryIPs(), Metrics: col.Metrics(),
			TopProcesses: col.TopProcesses(5)}
		if sendInventory || time.Since(lastInventory) >= invInterval {
			rep.Inventory = collectInventory()
		}
		resp, err := client.Report(rep)
		if err == nil {
			if failures > 0 {
				log.Printf("connection to server restored after %d failed attempt(s)", failures)
			}
			failures = 0
			if rep.Inventory != nil {
				lastInventory = time.Now()
			}
			sendInventory = resp.SendInventory
			if resp.ReportInterval > 0 {
				interval = clampSeconds(resp.ReportInterval, 5, 3600)
			}
			if resp.InventoryInterval > 0 {
				invInterval = clampSeconds(resp.InventoryInterval, 300, 86400)
			}
			runner.Handle(resp)
			wait = interval
			continue
		}

		var ae *apiError
		if errors.As(err, &ae) {
			switch ae.Status {
			case 401:
				log.Printf("server rejected this agent (%s); it may have been revoked. Retrying in 15 minutes.", ae.Detail)
				wait = 15 * time.Minute
				continue
			case 409: // server says the machine identity does not match this agent
				if fresh, rerr := reenroll(cfgPath, cfg); rerr == nil {
					cfg = fresh
					if client, err = newClient(cfg.Server, cfg.Fingerprint, cfg.Secret); err != nil {
						return err
					}
					runner.setClient(client, cfg)
					sendInventory, wait = true, 5*time.Second
				} else {
					log.Printf("%v", rerr)
					wait = 15 * time.Minute
				}
				continue
			case 413:
				log.Printf("inventory too large for the server; skipping it this cycle")
				lastInventory, sendInventory = time.Now(), false
			}
		}
		failures++
		if failures == 1 || failures%10 == 0 {
			log.Printf("report failed (attempt %d): %v", failures, err)
		}
		backoff := interval * time.Duration(failures)
		if backoff > 5*time.Minute {
			backoff = 5 * time.Minute
		}
		wait = backoff + time.Duration(rand.Intn(10))*time.Second
		if rep.Inventory != nil {
			sendInventory = true // retry the inventory with the next successful report
		}
	}
}
