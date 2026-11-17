//go:build windows

package main

import (
	"log"
	"runtime"
	"time"

	"golang.org/x/sys/windows"
)

const (
	esContinuous     = 0x80000000
	esSystemRequired = 0x00000001
)

var procSetThreadExecutionState = windows.NewLazySystemDLL("kernel32.dll").NewProc("SetThreadExecutionState")

// keepAwake stops Windows from going to idle sleep while the agent runs. A sleeping VM stops VMware Tools and the
// agent, so vCenter and vCentral lose its IP and metrics. The request lasts as long as the thread that made it,
// so it is held on a dedicated OS thread (shows in "powercfg /requests" under SYSTEM). It does not block a
// deliberate Sleep chosen by a user, and is skipped when the agent was installed with --allow-sleep.
func keepAwake(cfg *Config) {
	if cfg.AllowSleep {
		log.Printf("sleep allowed by configuration (--allow-sleep)")
		return
	}
	if procSetThreadExecutionState.Find() != nil {
		return
	}
	go func() {
		runtime.LockOSThread() // the request belongs to this thread; keep it alive and never reuse it
		for first := true; ; first = false {
			r, _, err := procSetThreadExecutionState.Call(esContinuous | esSystemRequired)
			if first {
				if r == 0 {
					log.Printf("could not ask Windows to stay awake: %v", err)
				} else {
					log.Printf("keeping this VM awake (Windows idle sleep is blocked while the agent runs)")
				}
			}
			time.Sleep(10 * time.Minute) // re-assert now and then; harmless if unchanged
		}
	}()
}
