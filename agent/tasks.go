package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

const (
	maxParallelTasks = 2
	outputLimit      = 1 << 20 // bytes of output kept per task
	progressEvery    = 3 * time.Second
	clockSkew        = 10 * time.Minute
)

var errRestart = errors.New("restarting to finish an agent update")

type taskPayload struct {
	V         int    `json:"v"`
	RunID     int64  `json:"run_id"`
	AgentID   string `json:"agent_id"`
	Kind      string `json:"kind"` // script | update
	Shell     string `json:"shell"`
	Script    string `json:"script"`
	Timeout   int    `json:"timeout"`
	IssuedAt  string `json:"issued_at"`
	ExpiresAt string `json:"expires_at"`
	Version   string `json:"version"` // update
	Path      string `json:"path"`
	SHA256    string `json:"sha256"`
	Size      int64  `json:"size"`
}

// TaskRunner checks and runs tasks sent by the server. A task runs only if it is signed with the key this agent
// trusts, is addressed to this agent, has not expired and has not run before.
type TaskRunner struct {
	mu        sync.Mutex
	client    *Client
	cfg       *Config
	cfgPath   string
	dir       string
	running   map[int64]chan struct{} // close to cancel
	executed  map[int64]time.Time
	slots     chan struct{}
	restart   chan struct{}
	keyWarned bool
}

func newTaskRunner(client *Client, cfg *Config, cfgPath string) *TaskRunner {
	dir := os.Getenv("VCENTRAL_AGENT_TASK_DIR") // for testing; normally the platform's private folder
	if dir == "" {
		dir = taskDir()
	}
	r := &TaskRunner{client: client, cfg: cfg, cfgPath: cfgPath, dir: dir, running: map[int64]chan struct{}{},
		executed: map[int64]time.Time{}, slots: make(chan struct{}, maxParallelTasks), restart: make(chan struct{}, 1)}
	r.loadExecuted()
	return r
}

func (r *TaskRunner) setClient(c *Client, cfg *Config) {
	r.mu.Lock()
	r.client, r.cfg = c, cfg
	r.mu.Unlock()
}

func (r *TaskRunner) executedPath() string {
	return filepath.Join(filepath.Dir(r.cfgPath), "executed-tasks.json")
}

func (r *TaskRunner) loadExecuted() {
	data, err := os.ReadFile(r.executedPath())
	if err != nil {
		return
	}
	var ids map[int64]time.Time
	if json.Unmarshal(data, &ids) == nil {
		r.executed = ids
	}
}

// markExecuted records the run before it starts, so a crash or replay can never run it twice.
func (r *TaskRunner) markExecuted(id int64) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, seen := r.executed[id]; seen {
		return false
	}
	r.executed[id] = time.Now()
	if len(r.executed) > 2000 { // keep the most recent ids
		type kv struct {
			id int64
			t  time.Time
		}
		all := make([]kv, 0, len(r.executed))
		for k, v := range r.executed {
			all = append(all, kv{k, v})
		}
		sort.Slice(all, func(i, j int) bool { return all[i].t.After(all[j].t) })
		r.executed = map[int64]time.Time{}
		for _, e := range all[:1000] {
			r.executed[e.id] = e.t
		}
	}
	if data, err := json.Marshal(r.executed); err == nil {
		_ = os.WriteFile(r.executedPath(), data, 0o600)
	}
	return true
}

// Handle processes the task part of a report response.
func (r *TaskRunner) Handle(resp *ReportResponse) {
	r.mu.Lock()
	cfg := r.cfg
	r.mu.Unlock()
	if resp.TaskPublicKey != "" && cfg.TaskKey == "" {
		cfg.TaskKey = resp.TaskPublicKey // trust on first use, over the pinned and authenticated connection
		if err := saveConfig(r.cfgPath, cfg); err != nil {
			log.Printf("could not save the task key: %v", err)
		} else {
			log.Printf("stored the server's task signing key")
		}
	} else if resp.TaskPublicKey != "" && resp.TaskPublicKey != cfg.TaskKey && !r.keyWarned {
		r.keyWarned = true
		log.Printf("WARNING: the server's task signing key changed; tasks are refused until the agent is reinstalled")
	}
	for _, t := range resp.Tasks {
		p, err := r.verify(cfg, t)
		if err != nil {
			log.Printf("rejected a task: %v", err)
			if p != nil { // genuine task we refuse (e.g. expired): tell the server why
				go r.finish(p.RunID, TaskResult{Error: err.Error()})
			}
			continue
		}
		if !r.markExecuted(p.RunID) {
			log.Printf("ignored task %d: it already ran on this machine", p.RunID)
			continue
		}
		if cfg.TasksDisabled {
			go r.finish(p.RunID, TaskResult{Error: "Tasks are disabled on this agent (installed with --disable-tasks)"})
			continue
		}
		go r.execute(p)
	}
	for _, id := range resp.Cancel {
		r.cancel(id)
	}
}

// verify returns the payload only if it is authentic. A payload with a valid signature that fails another check
// is also returned (with the error) so the refusal can be reported.
func (r *TaskRunner) verify(cfg *Config, t SignedTask) (*taskPayload, error) {
	if cfg.TaskKey == "" {
		return nil, errors.New("no task signing key yet")
	}
	key, err := base64.StdEncoding.DecodeString(cfg.TaskKey)
	if err != nil || len(key) != ed25519.PublicKeySize {
		return nil, errors.New("stored task key is invalid")
	}
	sig, err := base64.StdEncoding.DecodeString(t.Signature)
	if err != nil || !ed25519.Verify(ed25519.PublicKey(key), []byte(t.Payload), sig) {
		return nil, errors.New("bad signature")
	}
	var p taskPayload
	if err := json.Unmarshal([]byte(t.Payload), &p); err != nil {
		return nil, fmt.Errorf("unreadable task: %v", err)
	}
	if p.AgentID != cfg.AgentID {
		return nil, fmt.Errorf("task %d is addressed to another agent", p.RunID)
	}
	if exp, err := time.Parse(time.RFC3339, p.ExpiresAt); err != nil || time.Now().After(exp.Add(clockSkew)) {
		return &p, fmt.Errorf("task expired (check this machine's clock)")
	}
	if p.Kind != "script" && p.Kind != "update" {
		return &p, fmt.Errorf("unknown task kind %q", p.Kind)
	}
	return &p, nil
}

func (r *TaskRunner) cancel(id int64) {
	r.mu.Lock()
	ch, ok := r.running[id]
	if ok {
		delete(r.running, id)
	}
	r.mu.Unlock()
	if ok {
		close(ch)
	}
}

func (r *TaskRunner) currentClient() *Client {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.client
}

// finish sends the result, retrying for a while if vCentral is unreachable.
func (r *TaskRunner) finish(id int64, res TaskResult) {
	for attempt := 0; attempt < 30; attempt++ {
		err := r.currentClient().TaskResult(id, res)
		if err == nil {
			return
		}
		var ae *apiError
		if errors.As(err, &ae) && ae.Status < 500 && ae.Status != 429 {
			return // the server will never accept it (e.g. run deleted)
		}
		time.Sleep(time.Duration(5+attempt*5) * time.Second)
	}
	log.Printf("gave up sending the result of task %d", id)
}

func (r *TaskRunner) execute(p *taskPayload) {
	r.slots <- struct{}{}
	defer func() { <-r.slots }()
	switch p.Kind {
	case "update":
		r.runUpdate(p)
	default:
		r.runScript(p)
	}
}

// outputBuffer collects output and hands out what is new since the last call.
type outputBuffer struct {
	mu        sync.Mutex
	pending   bytes.Buffer
	total     int
	truncated bool
}

func (b *outputBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if room := outputLimit - b.total; room > 0 {
		chunk := p
		if len(chunk) > room {
			chunk, b.truncated = chunk[:room], true
		}
		b.pending.Write(chunk)
		b.total += len(chunk)
	} else {
		b.truncated = true
	}
	return len(p), nil
}

func (b *outputBuffer) take() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	s := b.pending.String()
	b.pending.Reset()
	return s
}

func (r *TaskRunner) runScript(p *taskPayload) {
	cmd, path, err := prepareScript(r.dir, p.RunID, p.Shell, p.Script)
	if err != nil {
		r.finish(p.RunID, TaskResult{Error: "could not start: " + err.Error()})
		return
	}
	defer os.Remove(path)
	out := &outputBuffer{}
	cmd.Stdout, cmd.Stderr = out, out
	cancel := make(chan struct{})
	r.mu.Lock()
	r.running[p.RunID] = cancel
	r.mu.Unlock()
	defer r.cancel(p.RunID)

	if err := cmd.Start(); err != nil {
		r.finish(p.RunID, TaskResult{Error: "could not start: " + err.Error()})
		return
	}
	log.Printf("running task %d (%s, timeout %ds)", p.RunID, p.Shell, p.Timeout)
	go func() { _, _ = r.currentClient().TaskProgress(p.RunID, "") }() // tells vCentral it started now
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	timeout := time.Duration(p.Timeout) * time.Second
	if timeout <= 0 {
		timeout = 5 * time.Minute
	}
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	ticker := time.NewTicker(progressEvery)
	defer ticker.Stop()

	res := TaskResult{}
	var waitErr error
loop:
	for {
		select {
		case waitErr = <-done:
			break loop
		case <-ticker.C:
			if chunk := out.take(); chunk != "" {
				if cancelled, err := r.currentClient().TaskProgress(p.RunID, chunk); err == nil && cancelled {
					r.cancel(p.RunID)
				}
			}
		case <-cancel:
			killTree(cmd)
			waitErr = <-done
			res.Cancelled = true
			break loop
		case <-deadline.C:
			killTree(cmd)
			waitErr = <-done
			res.TimedOut = true
			res.Error = fmt.Sprintf("stopped after the %s timeout", timeout)
			break loop
		}
	}
	res.Output = out.take()
	if out.truncated {
		res.Output += fmt.Sprintf("\n[vCentral: output beyond %d MB was not kept]\n", outputLimit>>20)
	}
	if cmd.ProcessState != nil && !res.TimedOut && !res.Cancelled {
		code := cmd.ProcessState.ExitCode()
		res.ExitCode = &code
	} else if waitErr != nil && !res.TimedOut && !res.Cancelled {
		res.Error = waitErr.Error()
	}
	r.finish(p.RunID, res)
}

func (r *TaskRunner) runUpdate(p *taskPayload) {
	zero := 0
	if p.Version == Version {
		r.finish(p.RunID, TaskResult{ExitCode: &zero, Output: "Already running " + Version + "\n"})
		return
	}
	self, err := os.Executable()
	if err == nil {
		self, err = filepath.EvalSymlinks(self)
	}
	if err != nil {
		r.finish(p.RunID, TaskResult{Error: "cannot locate the agent binary: " + err.Error()})
		return
	}
	data, err := r.currentClient().Download(p.Path, 200<<20)
	if err != nil {
		r.finish(p.RunID, TaskResult{Error: "download failed: " + err.Error()})
		return
	}
	sum := sha256.Sum256(data)
	if got := hex.EncodeToString(sum[:]); got != p.SHA256 {
		r.finish(p.RunID, TaskResult{Error: fmt.Sprintf("checksum mismatch (got %s…), update refused", got[:12])})
		return
	}
	if err := replaceExecutable(self, data); err != nil {
		r.finish(p.RunID, TaskResult{Error: "could not replace the agent binary: " + err.Error()})
		return
	}
	r.finish(p.RunID, TaskResult{ExitCode: &zero, Output: fmt.Sprintf("Updated %s -> %s; restarting\n", Version, p.Version)})
	log.Printf("agent updated to %s; restarting", p.Version)
	select {
	case r.restart <- struct{}{}:
	default:
	}
}
