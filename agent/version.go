package main

// Set at build time with -ldflags "-X main.Version=... -X main.Platform=...".
var (
	Version  = "dev"
	Platform = "" // e.g. linux-amd64, windows-legacy-amd64; tells the server which build to send for updates
)
