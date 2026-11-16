//go:build windows

package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

const serviceName = "vcentral-agent"

func envOr(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}

func dataDir() string { return filepath.Join(envOr("ProgramData", `C:\ProgramData`), "vCentral") }

func defaultConfigPath() string { return filepath.Join(dataDir(), "agent.json") }

func installBinPath() string {
	return filepath.Join(envOr("ProgramFiles", `C:\Program Files`), "vCentral", "vcentral-agent.exe")
}

func isAdmin() bool { return windows.GetCurrentProcessToken().IsElevated() }

// restrictPermissions limits the folder (and the secret inside) to SYSTEM and Administrators.
// SIDs are used instead of names so this works on non-English Windows.
func restrictPermissions(dir string) error {
	out, err := exec.Command("icacls", dir, "/inheritance:r",
		"/grant:r", "*S-1-5-18:(OI)(CI)F", "/grant:r", "*S-1-5-32-544:(OI)(CI)F").CombinedOutput()
	if err != nil {
		return fmt.Errorf("icacls: %v: %s", err, out)
	}
	return nil
}

// setupLogging sends logs to %ProgramData%\vCentral\agent.log when running as a service.
func setupLogging() {
	if inService, _ := svc.IsWindowsService(); !inService {
		return
	}
	path := filepath.Join(dataDir(), "agent.log")
	if st, err := os.Stat(path); err == nil && st.Size() > 5<<20 {
		_ = os.Rename(path, path+".old")
	}
	if f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600); err == nil {
		log.SetOutput(f)
	}
}

func requireServiceManager() error { return nil }

func stopService() {
	m, err := mgr.Connect()
	if err != nil {
		return
	}
	defer m.Disconnect()
	s, err := m.OpenService(serviceName)
	if err != nil {
		return
	}
	defer s.Close()
	if _, err := s.Control(svc.Stop); err != nil {
		return
	}
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		if st, err := s.Query(); err != nil || st.State == svc.Stopped {
			return
		}
		time.Sleep(500 * time.Millisecond)
	}
}

func installService() error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	exe := installBinPath()
	s, err := m.OpenService(serviceName)
	if err == nil {
		cfg, cerr := s.Config()
		if cerr != nil {
			s.Close()
			return cerr
		}
		cfg.BinaryPathName = `"` + exe + `" run`
		cfg.StartType = mgr.StartAutomatic
		cfg.DelayedAutoStart = true
		if err := s.UpdateConfig(cfg); err != nil {
			s.Close()
			return err
		}
	} else {
		s, err = m.CreateService(serviceName, exe, mgr.Config{
			DisplayName:      "vCentral Agent",
			Description:      "Reports health and inventory of this machine to vCentral.",
			StartType:        mgr.StartAutomatic,
			DelayedAutoStart: true,
		}, "run")
		if err != nil {
			return err
		}
	}
	defer s.Close()
	restart := mgr.RecoveryAction{Type: mgr.ServiceRestart, Delay: 10 * time.Second}
	_ = s.SetRecoveryActions([]mgr.RecoveryAction{restart, restart, restart}, 86400)
	// Also restart when the agent stops itself with an error, which is how it finishes a self-update.
	_ = s.SetRecoveryActionsOnNonCrashFailures(true)
	return s.Start()
}

func uninstallService() error {
	stopService()
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	s, err := m.OpenService(serviceName)
	if err != nil {
		return nil // not installed
	}
	defer s.Close()
	return s.Delete()
}

type agentService struct{ cfgPath string }

func (a *agentService) Execute(_ []string, req <-chan svc.ChangeRequest, status chan<- svc.Status) (bool, uint32) {
	status <- svc.Status{State: svc.StartPending}
	stop := make(chan struct{})
	done := make(chan error, 1)
	go func() { done <- runLoop(a.cfgPath, stop) }()
	status <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	for {
		select {
		case c := <-req:
			switch c.Cmd {
			case svc.Interrogate:
				status <- c.CurrentStatus
			case svc.Stop, svc.Shutdown:
				status <- svc.Status{State: svc.StopPending}
				close(stop)
				select {
				case <-done:
				case <-time.After(20 * time.Second):
				}
				return false, 0
			}
		case err := <-done:
			if err != nil {
				log.Printf("agent stopped: %v", err)
				return false, 1
			}
			return false, 0
		}
	}
}

func runService(cfgPath string) error {
	if inService, _ := svc.IsWindowsService(); inService {
		return svc.Run(serviceName, &agentService{cfgPath: cfgPath})
	}
	stop := make(chan struct{})
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt)
	go func() { <-sig; close(stop) }()
	return runLoop(cfgPath, stop)
}
