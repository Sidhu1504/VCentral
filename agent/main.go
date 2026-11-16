// vcentral-agent reports health metrics and inventory of a Linux or Windows machine to vCentral.
//
// It runs scripts and updates sent by vCentral only when they are signed with the server's task key, addressed to
// this agent, unexpired and not run before (see tasks.go); install with --disable-tasks to only report.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const usageText = `vCentral agent %s

Usage:
  vcentral-agent install --server URL --token TOKEN [--fingerprint SHA256] [--disable-tasks]
        Enroll this machine and install the agent as a service (run as root / Administrator).
        --disable-tasks: report only; never run scripts or updates sent by vCentral.
        --template: for a VM template (needs a reusable token). The template stays idle; every VM deployed
                    from it enrolls itself on first boot.
  vcentral-agent upgrade
        Replace an installed agent with this version and restart it. Keeps its identity and settings
        (no token needed). Run as root / Administrator. Works for every older version.
  vcentral-agent uninstall
        Stop and remove the service, its configuration and (on Linux) the binary.
  vcentral-agent status
        Show the configuration and test the connection to the server.
  vcentral-agent run [--config PATH]
        Run in the foreground (this is what the service runs).
  vcentral-agent enroll --server URL --token TOKEN --config PATH [--fingerprint SHA256]
        Enroll only and write a config file (for testing; no service is installed).
  vcentral-agent inventory | metrics
        Print what the agent would send, as JSON.
  vcentral-agent version
`

func usage() { fmt.Fprintf(os.Stderr, usageText, Version) }

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	cmd, args := os.Args[1], os.Args[2:]
	var err error
	switch cmd {
	case "install":
		err = cmdInstall(args)
	case "upgrade":
		err = cmdUpgrade()
	case "uninstall":
		err = cmdUninstall()
	case "status":
		err = cmdStatus(args)
	case "run":
		err = cmdRun(args)
	case "enroll":
		err = cmdEnroll(args)
	case "inventory":
		err = printJSON(collectInventory())
	case "metrics":
		c := NewCollector()
		c.Metrics()
		c.TopProcesses(5)
		time.Sleep(2 * time.Second)
		err = printJSON(map[string]interface{}{"metrics": c.Metrics(), "top_processes": c.TopProcesses(5)})
	case "version", "--version", "-v":
		fmt.Printf("%s (%s/%s, %s)\n", Version, runtime.GOOS, runtime.GOARCH, runtime.Version())
	case "help", "--help", "-h":
		usage()
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func printJSON(v interface{}) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func enrollFlags(name string, args []string, withConfig bool) (server, token, fp, cfgPath string, err error) {
	fs := flag.NewFlagSet(name, flag.ContinueOnError)
	fs.StringVar(&server, "server", "", "vCentral URL, e.g. https://vcentral.example.local")
	fs.StringVar(&token, "token", "", "enrollment token from the vCentral Agents page")
	fs.StringVar(&fp, "fingerprint", "", "SHA-256 fingerprint of the server certificate (for self-signed certificates)")
	if withConfig {
		fs.StringVar(&cfgPath, "config", "", "where to write the config file")
	}
	if err = fs.Parse(args); err != nil {
		return
	}
	if server == "" || token == "" || (withConfig && cfgPath == "") {
		err = errors.New("missing required flags; see 'vcentral-agent help'")
	}
	return
}

// popFlag removes a boolean flag (e.g. --disable-tasks) from args and reports whether it was there.
func popFlag(args []string, names ...string) ([]string, bool) {
	rest, found := []string{}, false
	for _, a := range args {
		match := false
		for _, n := range names {
			if a == n {
				match = true
			}
		}
		if match {
			found = true
		} else {
			rest = append(rest, a)
		}
	}
	return rest, found
}

func cmdInstall(args []string) error {
	args, disableTasks := popFlag(args, "--disable-tasks", "-disable-tasks")
	args, template := popFlag(args, "--template", "-template")
	server, token, fp, _, err := enrollFlags("install", args, false)
	if err != nil {
		return err
	}
	if !isAdmin() {
		return errors.New("install must run as root (Linux) or from an elevated prompt (Windows)")
	}
	if err := requireServiceManager(); err != nil {
		return err
	}
	var cfg *Config
	if template {
		// Do not enroll the template itself; check the token works so deployed VMs can enroll later.
		c, err := newClient(server, fp, "")
		if err != nil {
			return err
		}
		if err := c.CheckTemplateToken(token); err != nil {
			return err
		}
		bios, _ := biosIdentity()
		cfg = &Config{Server: c.server, Fingerprint: normalizeFingerprint(fp), EnrollToken: token, BiosUUID: bios, Template: true}
	} else {
		// Enroll first: if the token or connection is wrong, nothing on this machine has changed yet.
		if cfg, err = enroll(server, fp, token); err != nil {
			return err
		}
	}
	cfg.TasksDisabled = disableTasks
	stopService()
	self, err := os.Executable()
	if err != nil {
		return err
	}
	dest := installBinPath()
	if !samePath(self, dest) {
		if err := copyFile(self, dest); err != nil {
			return fmt.Errorf("copy binary to %s: %w", dest, err)
		}
	}
	if err := saveConfig(defaultConfigPath(), cfg); err != nil {
		return fmt.Errorf("write config: %w", err)
	}
	if err := installService(); err != nil {
		return fmt.Errorf("install service: %w", err)
	}
	if template {
		fmt.Printf("vCentral agent %s installed in template mode.\n  This machine stays idle; every VM deployed from it enrolls itself on first boot.\n"+
			"  Now shut it down and convert it to a template (on Windows, sysprep as usual).\n", Version)
		return nil
	}
	fmt.Printf("vCentral agent %s installed and running.\n  Agent ID: %s\n  Server:   %s\n", Version, cfg.AgentID, cfg.Server)
	return nil
}

// cmdUpgrade puts this binary in place of the installed agent and restarts the service. The config (agent ID,
// secret, server, pinned certificate) is kept, so it stays the same agent in vCentral. Also rewrites the service
// definition, so agents installed before self-update existed get the restart-on-exit behaviour it relies on.
func cmdUpgrade() error {
	if !isAdmin() {
		return errors.New("upgrade must run as root (Linux) or from an elevated prompt (Windows)")
	}
	if err := requireServiceManager(); err != nil {
		return err
	}
	cfgPath := defaultConfigPath()
	cfg, err := loadConfig(cfgPath)
	if err != nil {
		return fmt.Errorf("no installed vCentral agent found (%v); use the install command with an enrollment token instead", err)
	}
	self, err := os.Executable()
	if err != nil {
		return err
	}
	dest := installBinPath()
	backup := dest + ".previous"
	stopService()
	if !samePath(self, dest) {
		haveBackup := copyFile(dest, backup) == nil
		if err := copyFile(self, dest); err != nil {
			_ = installService() // bring the old version back up
			return fmt.Errorf("copy binary to %s: %w", dest, err)
		}
		defer os.Remove(backup)
		if err := installService(); err != nil {
			// Put the old version back so the VM is not left without a running agent.
			if haveBackup && copyFile(backup, dest) == nil {
				_ = installService()
				return fmt.Errorf("could not restart the service with the new version (%v); the previous version was restored", err)
			}
			return fmt.Errorf("restart service: %w", err)
		}
	} else if err := installService(); err != nil {
		return fmt.Errorf("restart service: %w", err)
	}
	who := cfg.AgentID
	if who == "" {
		who = "not enrolled yet (template mode)"
	}
	fmt.Printf("vCentral agent upgraded to %s and restarted.\n  Agent ID: %s (unchanged)\n  Server:   %s\n", Version, who, cfg.Server)
	return nil
}

func cmdUninstall() error {
	if !isAdmin() {
		return errors.New("uninstall must run as root (Linux) or from an elevated prompt (Windows)")
	}
	if err := uninstallService(); err != nil {
		return err
	}
	_ = os.RemoveAll(filepath.Dir(defaultConfigPath()))
	bin := installBinPath()
	if err := os.Remove(bin); err != nil && !os.IsNotExist(err) {
		fmt.Printf("Service removed. Delete %s manually to finish.\n", filepath.Dir(bin))
		return nil
	}
	if runtime.GOOS == "windows" {
		_ = os.Remove(filepath.Dir(bin))
	}
	fmt.Println("vCentral agent removed. Revoke it on the vCentral Agents page if it is still listed.")
	return nil
}

func cmdRun(args []string) error {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	cfgPath := fs.String("config", defaultConfigPath(), "config file")
	if err := fs.Parse(args); err != nil {
		return err
	}
	setupLogging()
	return runService(*cfgPath)
}

func cmdEnroll(args []string) error {
	server, token, fp, cfgPath, err := enrollFlags("enroll", args, true)
	if err != nil {
		return err
	}
	cfg, err := enroll(server, fp, token)
	if err != nil {
		return err
	}
	if err := saveConfig(cfgPath, cfg); err != nil {
		return err
	}
	fmt.Printf("Enrolled as %s; config written to %s\n", cfg.AgentID, cfgPath)
	return nil
}

func cmdStatus(args []string) error {
	fs := flag.NewFlagSet("status", flag.ContinueOnError)
	cfgPath := fs.String("config", defaultConfigPath(), "config file")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := loadConfig(*cfgPath)
	if err != nil {
		return fmt.Errorf("not enrolled (%v)", err)
	}
	pinned := "no (system certificate authorities)"
	if cfg.Fingerprint != "" {
		pinned = "yes"
	}
	current, _ := biosIdentity()
	fmt.Printf("Agent version: %s\nServer:        %s\nAgent ID:      %s\nCert pinned:   %s\n", Version, cfg.Server, cfg.AgentID, pinned)
	if !cfg.enrolled() {
		if cfg.Template && current != "" && current == cfg.BiosUUID {
			fmt.Println("Mode:          template (idle here; VMs deployed from this template enroll themselves)")
		} else {
			fmt.Println("Mode:          not enrolled yet; the service enrolls this machine automatically")
		}
	} else if cfg.BiosUUID != "" && current != "" && current != cfg.BiosUUID {
		fmt.Println("Identity:      CHANGED since enrollment (cloned machine?) — re-run install")
	}
	client, err := newClient(cfg.Server, cfg.Fingerprint, cfg.Secret)
	if err != nil {
		return err
	}
	if err := client.Ping(); err != nil {
		fmt.Printf("Connection:    FAILED (%v)\n", err)
		return errors.New("cannot reach server")
	}
	fmt.Println("Connection:    OK")
	return nil
}

func samePath(a, b string) bool {
	ra, err1 := filepath.EvalSymlinks(a)
	rb, err2 := filepath.EvalSymlinks(b)
	if err1 != nil || err2 != nil {
		return false
	}
	if runtime.GOOS == "windows" {
		return strings.EqualFold(ra, rb)
	}
	return ra == rb
}

func copyFile(src, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	tmp := dst + ".new"
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, dst)
}
