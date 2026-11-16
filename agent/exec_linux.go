//go:build linux

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
)

func taskDir() string { return "/var/lib/vcentral-agent/tasks" }

// prepareScript writes the script to a private file and returns the command that runs it.
// The command gets its own process group so a timeout or cancel can stop everything it started.
func prepareScript(dir string, id int64, shell, script string) (*exec.Cmd, string, error) {
	if shell != "bash" && shell != "sh" {
		return nil, "", fmt.Errorf("shell %q is not supported on Linux", shell)
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, "", err
	}
	path := filepath.Join(dir, fmt.Sprintf("task-%d.sh", id))
	if err := os.WriteFile(path, []byte(script), 0o700); err != nil {
		return nil, "", err
	}
	interp := "/bin/bash"
	if _, err := os.Stat(interp); err != nil || shell == "sh" {
		interp = "/bin/sh"
	}
	cmd := exec.Command(interp, path)
	cmd.Dir = dir
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Env = append(os.Environ(), "VCENTRAL_TASK_ID="+fmt.Sprint(id))
	return cmd, path, nil
}

func killTree(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
}

// replaceExecutable swaps the running binary for a new one (rename over it; Linux allows this).
func replaceExecutable(self string, data []byte) error {
	tmp := self + ".new"
	if err := os.WriteFile(tmp, data, 0o755); err != nil {
		return err
	}
	return os.Rename(tmp, self)
}

func cleanupOldExecutable(string) {}
