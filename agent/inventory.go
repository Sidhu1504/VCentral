package main

import (
	"bufio"
	"context"
	"net"
	"os"
	"os/exec"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v3/cpu"
	"github.com/shirou/gopsutil/v3/host"
	"github.com/shirou/gopsutil/v3/mem"
	gnet "github.com/shirou/gopsutil/v3/net"
	"github.com/shirou/gopsutil/v3/process"
)

// Facts identify the machine; they are sent at enrollment and with every inventory.
type Facts struct {
	Hostname      string   `json:"hostname"`
	OSFamily      string   `json:"os_family"`
	OSName        string   `json:"os_name"`
	OSVersion     string   `json:"os_version"`
	KernelVersion string   `json:"kernel_version"`
	Arch          string   `json:"arch"`
	BiosUUID      string   `json:"bios_uuid"`
	SerialNumber  string   `json:"serial_number"`
	MACs          []string `json:"macs"`
	IPs           []string `json:"ips"`
	AgentVersion  string   `json:"agent_version"`
}

type Interface struct {
	Name string   `json:"name"`
	MAC  string   `json:"mac"`
	IPs  []string `json:"ips"`
}

type Software struct {
	Name      string `json:"name"`
	Version   string `json:"version"`
	Publisher string `json:"publisher,omitempty"`
}

type Service struct {
	Name        string `json:"name"`
	DisplayName string `json:"display_name,omitempty"`
	State       string `json:"state"`
	StartMode   string `json:"start_mode,omitempty"`
}

type ListeningPort struct {
	Proto   string `json:"proto"`
	Address string `json:"address"`
	Port    uint32 `json:"port"`
	PID     int32  `json:"pid,omitempty"`
	Process string `json:"process,omitempty"`
}

type Inventory struct {
	Facts      Facts           `json:"facts"`
	CPUModel   string          `json:"cpu_model"`
	CPUCores   int             `json:"cpu_cores"`
	MemTotalMB uint64          `json:"mem_total_mb"`
	BootTime   string          `json:"boot_time,omitempty"`
	Timezone   string          `json:"timezone"`
	Interfaces []Interface     `json:"interfaces"`
	Software   []Software      `json:"software"`
	Services   []Service       `json:"services"`
	Ports      []ListeningPort `json:"listening_ports"`
	Errors     []string        `json:"errors,omitempty"` // parts that could not be collected
}

func collectFacts() Facts {
	f := Facts{OSFamily: runtime.GOOS, Arch: runtime.GOARCH, AgentVersion: Version}
	f.Hostname, _ = os.Hostname()
	if hi, err := host.Info(); err == nil {
		f.OSName = strings.TrimSpace(hi.Platform + " " + hi.PlatformVersion)
		f.OSVersion = hi.PlatformVersion
		f.KernelVersion = hi.KernelVersion
		if hi.KernelArch != "" {
			f.Arch = hi.KernelArch
		}
	}
	if pretty := osPrettyName(); pretty != "" {
		f.OSName = pretty
	}
	f.BiosUUID, f.SerialNumber = biosIdentity()
	for _, i := range interfaces() {
		if i.MAC != "" {
			f.MACs = append(f.MACs, i.MAC)
		}
		f.IPs = append(f.IPs, i.IPs...)
	}
	return f
}

func collectInventory() *Inventory {
	inv := &Inventory{Facts: collectFacts(), Interfaces: interfaces()}
	inv.Timezone, _ = time.Now().Zone()
	if infos, err := cpu.Info(); err == nil && len(infos) > 0 {
		inv.CPUModel = strings.TrimSpace(infos[0].ModelName)
	}
	inv.CPUCores, _ = cpu.Counts(true)
	if v, err := mem.VirtualMemory(); err == nil {
		inv.MemTotalMB = v.Total >> 20
	}
	if bt, err := host.BootTime(); err == nil && bt > 0 {
		inv.BootTime = time.Unix(int64(bt), 0).UTC().Format(time.RFC3339)
	}
	var err error
	if inv.Software, err = installedSoftware(); err != nil {
		inv.Errors = append(inv.Errors, "software: "+err.Error())
	}
	sort.Slice(inv.Software, func(i, j int) bool {
		return strings.ToLower(inv.Software[i].Name) < strings.ToLower(inv.Software[j].Name)
	})
	if inv.Services, err = services(); err != nil {
		inv.Errors = append(inv.Errors, "services: "+err.Error())
	}
	if inv.Ports, err = listeningPorts(); err != nil {
		inv.Errors = append(inv.Errors, "ports: "+err.Error())
	}
	return inv
}

func interfaces() []Interface {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	out := []Interface{}
	for _, ifc := range ifaces {
		if ifc.Flags&net.FlagLoopback != 0 {
			continue
		}
		i := Interface{Name: ifc.Name, MAC: strings.ToLower(ifc.HardwareAddr.String()), IPs: []string{}}
		addrs, _ := ifc.Addrs()
		for _, a := range addrs {
			ipnet, ok := a.(*net.IPNet)
			if !ok || ipnet.IP.IsLinkLocalUnicast() || ipnet.IP.IsLoopback() {
				continue
			}
			i.IPs = append(i.IPs, ipnet.IP.String())
		}
		if i.MAC == "" && len(i.IPs) == 0 {
			continue
		}
		out = append(out, i)
	}
	return out
}

// primaryIPs lists addresses on interfaces that are up and connected, ignoring loopback, link-local
// (169.254.x.x / fe80::, what Windows falls back to without DHCP) and container/virtual bridges, so an
// empty list really means "this machine has lost its network address".
func primaryIPs() []string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	out := []string{}
	for _, ifc := range ifaces {
		if ifc.Flags&net.FlagUp == 0 || ifc.Flags&net.FlagRunning == 0 || ifc.Flags&net.FlagLoopback != 0 {
			continue
		}
		name := strings.ToLower(ifc.Name)
		virtual := false
		for _, p := range skipNICPrefixes {
			if strings.HasPrefix(name, p) {
				virtual = true
				break
			}
		}
		if virtual {
			continue
		}
		addrs, _ := ifc.Addrs()
		for _, a := range addrs {
			if ipnet, ok := a.(*net.IPNet); ok && !ipnet.IP.IsLinkLocalUnicast() && !ipnet.IP.IsLoopback() {
				out = append(out, ipnet.IP.String())
			}
		}
	}
	return out
}

func listeningPorts() ([]ListeningPort, error) {
	conns, err := gnet.Connections("tcp")
	if err != nil {
		return nil, err
	}
	names := map[int32]string{}
	seen := map[string]bool{}
	out := []ListeningPort{}
	for _, c := range conns {
		if c.Status != "LISTEN" {
			continue
		}
		key := c.Laddr.IP + ":" + strconv.Itoa(int(c.Laddr.Port))
		if seen[key] {
			continue
		}
		seen[key] = true
		lp := ListeningPort{Proto: "tcp", Address: c.Laddr.IP, Port: c.Laddr.Port, PID: c.Pid}
		if c.Pid > 0 {
			if n, ok := names[c.Pid]; ok {
				lp.Process = n
			} else if p, err := process.NewProcess(c.Pid); err == nil {
				lp.Process, _ = p.Name()
				names[c.Pid] = lp.Process
			}
		}
		out = append(out, lp)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Port < out[j].Port })
	return out, nil
}

// runCommand runs a helper tool with a timeout and a stable (English) locale.
func runCommand(timeout time.Duration, name string, args ...string) ([]string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = append(os.Environ(), "LC_ALL=C", "LANG=C")
	out, err := cmd.Output()
	if err != nil {
		return nil, err
	}
	var lines []string
	sc := bufio.NewScanner(strings.NewReader(string(out)))
	sc.Buffer(make([]byte, 1024*1024), 1024*1024)
	for sc.Scan() {
		if line := strings.TrimRight(sc.Text(), "\r"); line != "" {
			lines = append(lines, line)
		}
	}
	return lines, nil
}
