cd E:\Xyron\backend\integrations\whatsapp\sidecar
Get-Content .env | ForEach-Object {
  if ($_ -match '^([^#=]+)=(.*)$') {
    [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
  }
}
node server.js *>&1 | Tee-Object -FilePath 'E:\Xyron\backend\integrations\whatsapp\sidecar\live_console.log'
