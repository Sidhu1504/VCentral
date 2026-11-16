import { Alert, Tabs, Typography } from 'antd'

/**
 * Ready-to-paste install commands. With a fingerprint, both commands verify vCentral's certificate
 * before downloading anything, so a self-signed certificate is safe to use.
 */
export function buildCommands(token, fingerprint, serverUrl, template = false) {
  const t = template ? ' --template' : ''
  const server = (serverUrl || window.location.origin).replace(/\/$/, '')
  const u = new URL(server)
  const host = u.host
  const hostname = u.hostname
  const hostPort = u.port ? host : `${host}:443`
  const linux = fingerprint
    ? `echo | openssl s_client -connect ${hostPort} -servername ${hostname} 2>/dev/null | openssl x509 > /tmp/vcentral.pem && ` +
      `[ "$(openssl x509 -in /tmp/vcentral.pem -outform DER | openssl dgst -sha256 | sed 's/.*= //')" = "${fingerprint}" ] && ` +
      `curl -fsSL --cacert /tmp/vcentral.pem ${server}/api/agent/install.sh | sudo sh -s -- --server ${server} --token ${token} --fingerprint ${fingerprint} --cacert /tmp/vcentral.pem${t} ` +
      `|| echo "vCentral agent was not installed (see the message above; no message = certificate check failed)"`
    : `curl -fsSL ${server}/api/agent/install.sh | sudo sh -s -- --server ${server} --token ${token}${t}`
  const pin = fingerprint
    ? `$fp='${fingerprint}'; [Net.ServicePointManager]::ServerCertificateValidationCallback = { param($s, $c) (-join ([Security.Cryptography.SHA256]::Create().ComputeHash($c.GetRawCertData()) | ForEach-Object { $_.ToString('x2') })) -eq $fp }; `
    : ''
  const windows =
    `[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; ${pin}` +
    `iex ((New-Object Net.WebClient).DownloadString('${server}/api/agent/install.ps1')); ` +
    `Install-VCentralAgent -Server '${server}' -Token '${token}'${fingerprint ? ' -Fingerprint $fp' : ''}${template ? ' -Template' : ''}`
  const manual = `vcentral-agent install --server ${server} --token ${token}${fingerprint ? ` --fingerprint ${fingerprint}` : ''}${t}`
  return { linux, windows, manual }
}

/**
 * Commands that replace an installed agent (any version, including ones too old to update themselves) with the
 * version on this vCentral. No token: the agent keeps its identity, settings and history.
 */
export function buildUpgradeCommands(fingerprint, serverUrl) {
  const server = (serverUrl || window.location.origin).replace(/\/$/, '')
  const u = new URL(server)
  const hostPort = u.port ? u.host : `${u.host}:443`
  const linux = fingerprint
    ? `echo | openssl s_client -connect ${hostPort} -servername ${u.hostname} 2>/dev/null | openssl x509 > /tmp/vcentral.pem && ` +
      `[ "$(openssl x509 -in /tmp/vcentral.pem -outform DER | openssl dgst -sha256 | sed 's/.*= //')" = "${fingerprint}" ] && ` +
      `curl -fsSL --cacert /tmp/vcentral.pem ${server}/api/agent/install.sh | sudo sh -s -- --server ${server} --upgrade --cacert /tmp/vcentral.pem ` +
      `|| echo "vCentral agent was not updated (see the message above; no message = certificate check failed)"`
    : `curl -fsSL ${server}/api/agent/install.sh | sudo sh -s -- --server ${server} --upgrade`
  const pin = fingerprint
    ? `$fp='${fingerprint}'; [Net.ServicePointManager]::ServerCertificateValidationCallback = { param($s, $c) (-join ([Security.Cryptography.SHA256]::Create().ComputeHash($c.GetRawCertData()) | ForEach-Object { $_.ToString('x2') })) -eq $fp }; `
    : ''
  const windows =
    `[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; ${pin}` +
    `iex ((New-Object Net.WebClient).DownloadString('${server}/api/agent/install.ps1')); Update-VCentralAgent -Server '${server}'`
  return { linux, windows }
}

const codeStyle = { display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-all', padding: 12, fontSize: 12 }

export default function InstallCommands({ token, fingerprint, server, template = false }) {
  const cmd = buildCommands(token, fingerprint, server, template)
  const block = (text) => (
    <Typography.Paragraph copyable={{ text }} style={{ marginBottom: 8 }}>
      <code style={codeStyle}>{text}</code>
    </Typography.Paragraph>
  )
  return (
    <>
      <Tabs
        items={[
          {
            key: 'linux', label: 'Linux',
            children: (
              <>
                <Typography.Paragraph type="secondary">Run on the VM as a user with sudo. Needs systemd, curl (or wget) and openssl.</Typography.Paragraph>
                {block(cmd.linux)}
              </>
            ),
          },
          {
            key: 'windows', label: 'Windows',
            children: (
              <>
                <Typography.Paragraph type="secondary">
                  Paste into PowerShell opened with <b>Run as administrator</b>. Picks the right build for Windows Server 2008 R2 to 2022.
                  Old systems without .NET 4.5 cannot use TLS 1.2 from PowerShell; use the manual install below instead.
                </Typography.Paragraph>
                {block(cmd.windows)}
              </>
            ),
          },
          {
            key: 'manual', label: 'Manual',
            children: (
              <>
                <Typography.Paragraph type="secondary">
                  Copy the agent file for the VM's platform from the Downloads tab, then run (as root / Administrator):
                </Typography.Paragraph>
                {block(cmd.manual)}
              </>
            ),
          },
        ]}
      />
      {!fingerprint && (
        <Alert type="info" showIcon message="Certificate pinning is off: VMs must trust vCentral's HTTPS certificate (a certificate from your CA)." />
      )}
    </>
  )
}
