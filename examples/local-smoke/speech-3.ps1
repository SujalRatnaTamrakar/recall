Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile('C:\Users\sujal\Documents\Codex\2026-10-28\new-chat\outputs\tube-live\examples\local-smoke\speech-3.wav')
$s.Speak('Next I will add captions to this design.')
$s.Dispose()
