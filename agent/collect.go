package main

import (
	"context"
	"os"
	"runtime"
	"sort"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v3/cpu"
	"github.com/shirou/gopsutil/v3/disk"
	"github.com/shirou/gopsutil/v3/host"
	"github.com/shirou/gopsutil/v3/load"
	"github.com/shirou/gopsutil/v3/mem"
	gnet "github.com/shirou/gopsutil/v3/net"
	"github.com/shirou/gopsutil/v3/process"
)

type DiskUsage struct {
	Mount   string  `json:"mount"`
	FSType  string  `json:"fstype"`
	TotalGB float64 `json:"total_gb"`
	UsedGB  float64 `json:"used_gb"`
	UsedPct float64 `json:"used_pct"`
}

type Metrics struct {
	CPUPct        float64     `json:"cpu_pct"`
	MemPct        float64     `json:"mem_pct"`
	MemTotalMB    uint64      `json:"mem_total_mb"`
	MemUsedMB     uint64      `json:"mem_used_mb"`
	SwapPct       *float64    `json:"swap_pct"`
	Load1         *float64    `json:"load1"`
	Load5         *float64    `json:"load5"`
	Load15        *float64    `json:"load15"`
	UptimeSeconds uint64      `json:"uptime_seconds"`
	ProcessCount  int         `json:"process_count"`
	Disks         []DiskUsage `json:"disks"`
	DiskReadBps   *float64    `json:"disk_read_bps"`
	DiskWriteBps  *float64    `json:"disk_write_bps"`
	NetRxBps      *float64    `json:"net_rx_bps"`
	NetTxBps      *float64    `json:"net_tx_bps"`
}

type ProcInfo struct {
	PID    int32   `json:"pid"`
	Name   string  `json:"name"`
	User   string  `json:"user,omitempty"`
	CPUPct float64 `json:"cpu_pct"`
	MemMB  float64 `json:"mem_mb"`
}

type TopProcesses struct {
	ByCPU []ProcInfo `json:"by_cpu"`
	ByMem []ProcInfo `json:"by_mem"`
}

// Collector keeps the previous counters so rates (bytes/s, process CPU) can be computed.
type Collector struct {
	prevTime            time.Time
	prevRead, prevWrite uint64
	prevRx, prevTx      uint64
	havePrevIO          bool
	prevProcCPU         map[int32]float64
	prevProcTime        time.Time
}

func NewCollector() *Collector {
	c := &Collector{prevProcCPU: map[int32]float64{}}
	cpu.Percent(0, false) // prime the CPU counter so the first real sample covers one interval
	return c
}

func round1(v float64) float64 { return float64(int64(v*10+0.5)) / 10 }

func ptr(v float64) *float64 { r := round1(v); return &r }

func (c *Collector) Metrics() Metrics {
	var m Metrics
	if p, err := cpu.Percent(0, false); err == nil && len(p) > 0 {
		m.CPUPct = round1(p[0])
	}
	if v, err := mem.VirtualMemory(); err == nil {
		m.MemTotalMB = v.Total >> 20
		m.MemUsedMB = (v.Total - v.Available) >> 20
		if v.Total > 0 {
			m.MemPct = round1(float64(v.Total-v.Available) / float64(v.Total) * 100)
		}
	}
	if s, err := mem.SwapMemory(); err == nil && s.Total > 0 {
		m.SwapPct = ptr(s.UsedPercent)
	}
	if runtime.GOOS != "windows" {
		if l, err := load.Avg(); err == nil {
			m.Load1, m.Load5, m.Load15 = ptr(l.Load1), ptr(l.Load5), ptr(l.Load15)
		}
	}
	if up, err := host.Uptime(); err == nil {
		m.UptimeSeconds = up
	}
	if pids, err := process.Pids(); err == nil {
		m.ProcessCount = len(pids)
	}
	m.Disks = diskUsage()

	now := time.Now()
	read, write := diskIOTotals()
	rx, tx := netTotals()
	if c.havePrevIO {
		if secs := now.Sub(c.prevTime).Seconds(); secs > 0 {
			m.DiskReadBps = rate(read, c.prevRead, secs)
			m.DiskWriteBps = rate(write, c.prevWrite, secs)
			m.NetRxBps = rate(rx, c.prevRx, secs)
			m.NetTxBps = rate(tx, c.prevTx, secs)
		}
	}
	c.prevTime, c.prevRead, c.prevWrite, c.prevRx, c.prevTx, c.havePrevIO = now, read, write, rx, tx, true
	return m
}

func rate(cur, prev uint64, secs float64) *float64 {
	if cur < prev { // counter reset (reboot, NIC re-plugged)
		return nil
	}
	return ptr(float64(cur-prev) / secs)
}

var skipFSTypes = map[string]bool{
	"tmpfs": true, "devtmpfs": true, "overlay": true, "squashfs": true, "proc": true, "sysfs": true,
	"nsfs": true, "tracefs": true, "autofs": true, "ramfs": true, "efivarfs": true, "iso9660": true,
	"cdfs": true, "udf": true, "nfs": true, "nfs4": true, "cifs": true, "smbfs": true, "fuse.sshfs": true,
}

func diskUsage() []DiskUsage {
	parts, err := disk.Partitions(false)
	if err != nil {
		return nil
	}
	seen := map[string]bool{}
	out := []DiskUsage{}
	for _, p := range parts {
		if skipFSTypes[strings.ToLower(p.Fstype)] || strings.HasPrefix(p.Mountpoint, "/snap/") || seen[p.Device] {
			continue
		}
		seen[p.Device] = true
		u := usageWithTimeout(p.Mountpoint, 3*time.Second)
		if u == nil || u.Total == 0 {
			continue
		}
		// Same figures as `df` and Windows Explorer: on Linux, blocks reserved for root (usually 5% on ext4)
		// are neither used nor available, and Use% = used / (used + available).
		out = append(out, DiskUsage{
			Mount: p.Mountpoint, FSType: p.Fstype,
			TotalGB: round1(float64(u.Total) / (1 << 30)), UsedGB: round1(float64(u.Used) / (1 << 30)),
			UsedPct: round1(u.UsedPercent),
		})
	}
	return out
}

// usageWithTimeout guards against hung network or stale mounts blocking the whole agent.
func usageWithTimeout(path string, d time.Duration) *disk.UsageStat {
	ctx, cancel := context.WithTimeout(context.Background(), d)
	defer cancel()
	ch := make(chan *disk.UsageStat, 1)
	go func() {
		u, err := disk.Usage(path)
		if err != nil {
			u = nil
		}
		ch <- u
	}()
	select {
	case u := <-ch:
		return u
	case <-ctx.Done():
		return nil
	}
}

func diskIOTotals() (read, write uint64) {
	counters, err := disk.IOCounters()
	if err != nil {
		return 0, 0
	}
	for name, s := range counters {
		if runtime.GOOS == "linux" {
			// Count whole physical disks only: partitions, LVM (dm-*), loop and optical devices would double count.
			if _, err := os.Stat("/sys/block/" + name); err != nil {
				continue
			}
			if strings.HasPrefix(name, "loop") || strings.HasPrefix(name, "ram") || strings.HasPrefix(name, "dm-") ||
				strings.HasPrefix(name, "sr") || strings.HasPrefix(name, "md") {
				continue
			}
		}
		read += s.ReadBytes
		write += s.WriteBytes
	}
	return
}

var skipNICPrefixes = []string{"lo", "loopback", "isatap", "teredo", "veth", "docker", "br-", "virbr", "cni", "flannel", "cali"}

func netTotals() (rx, tx uint64) {
	counters, err := gnet.IOCounters(true)
	if err != nil {
		return 0, 0
	}
	for _, s := range counters {
		name := strings.ToLower(s.Name)
		skip := false
		for _, p := range skipNICPrefixes {
			if strings.HasPrefix(name, p) {
				skip = true
				break
			}
		}
		if !skip {
			rx += s.BytesRecv
			tx += s.BytesSent
		}
	}
	return
}

// TopProcesses ranks processes by CPU used since the previous call, and by resident memory.
func (c *Collector) TopProcesses(n int) TopProcesses {
	procs, err := process.Processes()
	if err != nil {
		return TopProcesses{}
	}
	now := time.Now()
	elapsed := now.Sub(c.prevProcTime).Seconds()
	havePrev := !c.prevProcTime.IsZero() && elapsed > 0
	ncpu := float64(runtime.NumCPU())

	type entry struct {
		p     *process.Process
		cpu   float64
		memMB float64
	}
	cur := make(map[int32]float64, len(procs))
	entries := make([]entry, 0, len(procs))
	for _, p := range procs {
		e := entry{p: p}
		if t, err := p.Times(); err == nil {
			total := t.User + t.System
			cur[p.Pid] = total
			if prev, ok := c.prevProcCPU[p.Pid]; ok && havePrev && total >= prev {
				e.cpu = (total - prev) / elapsed / ncpu * 100
			}
		}
		if mi, err := p.MemoryInfo(); err == nil {
			e.memMB = float64(mi.RSS) / (1 << 20)
		}
		entries = append(entries, e)
	}
	c.prevProcCPU, c.prevProcTime = cur, now

	info := func(e entry) ProcInfo {
		name, _ := e.p.Name()
		user, _ := e.p.Username()
		return ProcInfo{PID: e.p.Pid, Name: name, User: user, CPUPct: round1(e.cpu), MemMB: round1(e.memMB)}
	}
	var out TopProcesses
	if havePrev {
		sort.Slice(entries, func(i, j int) bool { return entries[i].cpu > entries[j].cpu })
		for i := 0; i < len(entries) && i < n; i++ {
			out.ByCPU = append(out.ByCPU, info(entries[i]))
		}
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].memMB > entries[j].memMB })
	for i := 0; i < len(entries) && i < n; i++ {
		out.ByMem = append(out.ByMem, info(entries[i]))
	}
	return out
}
