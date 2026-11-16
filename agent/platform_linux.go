//go:build linux

package main

import (
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
)

const (
	serviceName  = "vcentral-agent"
	unitPath     = "/etc/systemd/system/vcentral-agent.service"
	linuxBinPath = "/usr/local/bin/vcentral-agent"
)

func defaultConfigPath() string { return "/etc/vcentral-agent/agent.json" }

func installBinPath() string { return linuxBinPath }

func isAdmin() bool { return os.Geteuid() == 0 }

func restrictPermissions(dir string) error { return os.Chmod(dir, 0o700) }

func setupLogging() {} // systemd journal captures stderr

const unitFile = `[Unit]
Description=vCentral Agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=` + linuxBinPath + ` run
Restart=always
RestartSec=10
Nice=10

[Install]
WantedBy=multi-user.target
`

func systemctl(args ...string) error {
	out, err := exec.Command("systemctl", args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("systemctl %v: %v: %s", args, err, out)
	}
	return nil
}

func requireServiceManager() error {
	if _, err := exec.LookPath("systemctl"); err != nil {
		return errors.New("systemd is required (systemctl not found)")
	}
	return nil
}

func stopService() {
	_ = exec.Command("systemctl", "stop", serviceName).Run()
}

func installService() error {
	if err := os.WriteFile(unitPath, []byte(unitFile), 0o644); err != nil {
		return err
	}
	if err := systemctl("daemon-reload"); err != nil {
		return err
	}
	if err := systemctl("enable", serviceName); err != nil {
		return err
	}
	return systemctl("restart", serviceName)
}

func uninstallService() error {
	_ = exec.Command("systemctl", "disable", "--now", serviceName).Run()
	if err := os.Remove(unitPath); err != nil && !os.IsNotExist(err) {
		return err
	}
	return systemctl("daemon-reload")
}

// runService runs the report loop until SIGTERM/SIGINT.
func runService(cfgPath string) error {
	stop := make(chan struct{})
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		s := <-sig
		log.Printf("received %s, stopping", s)
		close(stop)
	}()
	return runLoop(cfgPath, stop)
}
