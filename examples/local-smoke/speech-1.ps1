Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile('C:\Users\sujal\Documents\Codex\2026-10-28\new-chat\outputs\tube-live\examples\local-smoke\speech-1.wav')
$s.Speak('I enabled the preview because the first attempt was blank.')
$s.Dispose()
