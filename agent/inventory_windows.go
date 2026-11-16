//go:build windows

package main

import (
	"strings"

	"github.com/yusufpapurcu/wmi"
	"golang.org/x/sys/windows/registry"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

type win32ComputerSystemProduct struct {
	UUID              string
	IdentifyingNumber string
}

func biosIdentity() (uuid, serial string) {
	var dst []win32ComputerSystemProduct
	if err := wmi.Query("SELECT UUID, IdentifyingNumber FROM Win32_ComputerSystemProduct", &dst); err != nil || len(dst) == 0 {
		return "", ""
	}
	return strings.ToLower(strings.TrimSpace(dst[0].UUID)), strings.TrimSpace(dst[0].IdentifyingNumber)
}

func osPrettyName() string { return "" } // gopsutil's "Windows Server 2019 Standard 10.0.17763 ..." is used

const uninstallKey = `SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`

// installedSoftware reads "Programs and Features" from both the 64-bit and 32-bit registry views.
func installedSoftware() ([]Software, error) {
	seen := map[string]bool{}
	out := []Software{}
	var firstErr error
	for _, view := range []uint32{registry.WOW64_64KEY, registry.WOW64_32KEY} {
		k, err := registry.OpenKey(registry.LOCAL_MACHINE, uninstallKey, registry.ENUMERATE_SUB_KEYS|registry.QUERY_VALUE|view)
		if err != nil {
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		names, _ := k.ReadSubKeyNames(-1)
		for _, n := range names {
			sk, err := registry.OpenKey(k, n, registry.QUERY_VALUE|view)
			if err != nil {
				continue
			}
			name, _, _ := sk.GetStringValue("DisplayName")
			sysComp, _, _ := sk.GetIntegerValue("SystemComponent")
			parent, _, _ := sk.GetStringValue("ParentKeyName") // updates/patches of another product
			version, _, _ := sk.GetStringValue("DisplayVersion")
			publisher, _, _ := sk.GetStringValue("Publisher")
			sk.Close()
			name = strings.TrimSpace(name)
			if name == "" || sysComp == 1 || parent != "" || seen[name+"|"+version] {
				continue
			}
			seen[name+"|"+version] = true
			out = append(out, Software{Name: name, Version: version, Publisher: publisher})
		}
		k.Close()
	}
	if len(out) == 0 && firstErr != nil {
		return nil, firstErr
	}
	return out, nil
}

var serviceStates = map[svc.State]string{
	svc.Stopped: "stopped", svc.StartPending: "starting", svc.StopPending: "stopping", svc.Running: "running",
	svc.ContinuePending: "resuming", svc.PausePending: "pausing", svc.Paused: "paused",
}

var startModes = map[uint32]string{
	mgr.StartAutomatic: "auto", mgr.StartManual: "manual", mgr.StartDisabled: "disabled", 0: "boot", 1: "system",
}

func services() ([]Service, error) {
	m, err := mgr.Connect()
	if err != nil {
		return nil, err
	}
	defer m.Disconnect()
	names, err := m.ListServices()
	if err != nil {
		return nil, err
	}
	out := make([]Service, 0, len(names))
	for _, n := range names {
		s, err := m.OpenService(n)
		if err != nil {
			continue
		}
		item := Service{Name: n, State: "unknown"}
		if cfg, err := s.Config(); err == nil {
			item.DisplayName = cfg.DisplayName
			item.StartMode = startModes[cfg.StartType]
			if cfg.StartType == mgr.StartAutomatic && cfg.DelayedAutoStart {
				item.StartMode = "auto (delayed)"
			}
		}
		if st, err := s.Query(); err == nil {
			item.State = serviceStates[st.State]
		}
		s.Close()
		out = append(out, item)
	}
	return out, nil
}
