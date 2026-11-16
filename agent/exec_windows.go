//go:build windows

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
)

// Inside %ProgramData%\vCentral, whose permissions (SYSTEM and Administrators only) are inherited.
func taskDir() string { return filepath.Join(dataDir(), "tasks") }

func powershellPath() string {
	root := os.Getenv("SystemRoot")
	if root == "" {
		root = `C:\Windows`
	}
	return filepath.Join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
}

func prepareScript(dir string, id int64, shell, script string) (*exec.Cmd, string, error) {
	if shell != "powershell" {
		return nil, "", fmt.Errorf("shell %q is not supported on Windows", shell)
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, "", err
	}
	path := filepath.Join(dir, fmt.Sprintf("task-%d.ps1", id))
	// UTF-8 with BOM so Windows PowerShell 2.0-5.1 reads non-ASCII characters correctly.
	data := append([]byte{0xEF, 0xBB, 0xBF}, []byte(script)...)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return nil, "", err
	}
	cmd := exec.Command(powershellPath(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "VCENTRAL_TASK_ID="+fmt.Sprint(id))
	return cmd, path, nil
}

func killTree(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(cmd.Process.Pid)).Run()
	}
}

// replaceExecutable: Windows cannot overwrite a running .exe, but it can rename it.
func replaceExecutable(self string, data []byte) error {
	old := self + ".old"
	_ = os.Remove(old)
	if err := os.Rename(self, old); err != nil {
		return err
	}
	if err := os.WriteFile(self, data, 0o755); err != nil {
		_ = os.Rename(old, self)
		return err
	}
	return nil
}

func cleanupOldExecutable(self string) { _ = os.Remove(self + ".old") }
