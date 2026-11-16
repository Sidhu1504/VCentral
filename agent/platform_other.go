//go:build !linux && !windows

package main

import (
	"errors"
	"os"
	"os/exec"
	"os/signal"
)

// Development stubs so the agent builds on other systems (e.g. macOS); only Linux and Windows are supported.

const serviceName = "vcentral-agent"

var errUnsupported = errors.New("only Linux and Windows are supported")

func biosIdentity() (string, string)         { return "", "" }
func osPrettyName() string                   { return "" }
func installedSoftware() ([]Software, error) { return nil, errUnsupported }
func services() ([]Service, error)           { return nil, errUnsupported }
func defaultConfigPath() string              { return "agent.json" }
func installBinPath() string                 { return "" }
func isAdmin() bool                          { return false }
func restrictPermissions(string) error       { return nil }
func setupLogging()                          {}
func requireServiceManager() error           { return errUnsupported }
func stopService()                           {}
func installService() error                  { return errUnsupported }
func uninstallService() error                { return errUnsupported }

func taskDir() string { return "tasks" }
func prepareScript(string, int64, string, string) (*exec.Cmd, string, error) {
	return nil, "", errUnsupported
}
func killTree(*exec.Cmd)                     {}
func replaceExecutable(string, []byte) error { return errUnsupported }
func cleanupOldExecutable(string)            {}

func runService(cfgPath string) error {
	stop := make(chan struct{})
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt)
	go func() { <-sig; close(stop) }()
	return runLoop(cfgPath, stop)
}
