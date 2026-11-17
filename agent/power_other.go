//go:build !windows

package main

// keepAwake: Linux servers do not suspend on idle by default, so there is nothing to do.
func keepAwake(cfg *Config) {}
