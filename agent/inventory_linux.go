//go:build linux

package main

import (
	"bufio"
	"errors"
	"os"
	"os/exec"
	"strings"
	"time"
)

func readTrim(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func biosIdentity() (uuid, serial string) {
	return strings.ToLower(readTrim("/sys/class/dmi/id/product_uuid")), readTrim("/sys/class/dmi/id/product_serial")
}

func osPrettyName() string {
	f, err := os.Open("/etc/os-release")
	if err != nil {
		return ""
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if strings.HasPrefix(line, "PRETTY_NAME=") {
			return strings.Trim(strings.TrimPrefix(line, "PRETTY_NAME="), `"'`)
		}
	}
	return ""
}

func installedSoftware() ([]Software, error) {
	if _, err := exec.LookPath("rpm"); err == nil {
		lines, err := runCommand(2*time.Minute, "rpm", "-qa", "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{VENDOR}\n")
		if err == nil {
			return parseTabbed(lines, 0), nil
		}
		if _, derr := exec.LookPath("dpkg-query"); derr != nil {
			return nil, err
		}
	}
	if _, err := exec.LookPath("dpkg-query"); err == nil {
		lines, err := runCommand(2*time.Minute, "dpkg-query", "-W", "-f", "${db:Status-Abbrev}\t${Package}\t${Version}\t${Maintainer}\n")
		if err != nil {
			return nil, err
		}
		installed := lines[:0]
		for _, l := range lines {
			if strings.HasPrefix(l, "ii") || strings.HasPrefix(l, "hi") { // installed (or held)
				installed = append(installed, l)
			}
		}
		return parseTabbed(installed, 1), nil
	}
	return nil, errors.New("no rpm or dpkg found")
}

func parseTabbed(lines []string, skip int) []Software {
	out := make([]Software, 0, len(lines))
	for _, l := range lines {
		f := strings.Split(l, "\t")
		if len(f) < skip+2 {
			continue
		}
		s := Software{Name: f[skip], Version: f[skip+1]}
		if len(f) > skip+2 && f[skip+2] != "(none)" {
			s.Publisher = f[skip+2]
		}
		out = append(out, s)
	}
	return out
}

func services() ([]Service, error) {
	if _, err := exec.LookPath("systemctl"); err != nil {
		return nil, errors.New("systemctl not found")
	}
	units, err := runCommand(time.Minute, "systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain")
	if err != nil {
		return nil, err
	}
	modes := map[string]string{}
	if files, err := runCommand(time.Minute, "systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"); err == nil {
		for _, l := range files {
			if f := strings.Fields(l); len(f) >= 2 {
				modes[f[0]] = f[1]
			}
		}
	}
	out := []Service{}
	for _, l := range units {
		f := strings.Fields(l)
		if len(f) < 4 || f[1] == "not-found" {
			continue
		}
		s := Service{Name: strings.TrimSuffix(f[0], ".service"), State: f[2] + "/" + f[3], StartMode: modes[f[0]]}
		if len(f) > 4 {
			s.DisplayName = strings.Join(f[4:], " ")
		}
		switch {
		case f[2] == "active" && f[3] == "running":
			s.State = "running"
		case f[2] == "inactive":
			s.State = "stopped"
		case f[2] == "failed":
			s.State = "failed"
		}
		out = append(out, s)
	}
	return out, nil
}
