$ErrorActionPreference = "Stop"

# This setting is read by the Ollama server process, not by Chainlit.
$env:OLLAMA_MAX_LOADED_MODELS = "1"

Write-Host "Starting Ollama with OLLAMA_MAX_LOADED_MODELS=1"
Write-Host "Only one model will be kept loaded at a time."

& ollama serve
