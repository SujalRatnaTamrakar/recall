Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile('C:\Users\sujal\Documents\Codex\2026-10-28\new-chat\outputs\tube-live\examples\local-smoke\speech-0.wav')
$s.Speak('This is a synthetic local test. The first preview is off.')
$s.Dispose()
