Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile('C:\Users\sujal\Documents\Codex\2026-10-28\new-chat\outputs\tube-live\examples\local-smoke\speech-2.wav')
$s.Speak('The preview now works. Look at the screen for a visual change.')
$s.Dispose()
