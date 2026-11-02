"""Validated local experiment settings and bounded, content-free result history."""
from __future__ import annotations
import ctypes
import ctypes.util
import json
import os
from pathlib import Path

DEFAULTS = {'enabled': False, 'model': 'qwen3.5:9b', 'speech_model': 'small',
            'speech_device': 'cpu', 'cpu_threads': 8, 'frame_interval_s': 5,
            'frame_width': 960, 'answer_style': 'natural', 'allow_predictions': True,
            'catch_up':'complete','vision_detail':'detailed','model_cpu_threads':4}
SPEECH_MODELS = ('tiny','tiny.en','base','base.en','small','small.en')

def speech_options(cache: Path) -> list[dict]:
    return [{'name':name, 'ready':any((cache/'models'/f'models--Systran--faster-whisper-{name}').glob('snapshots/*/model.bin'))}
            for name in SPEECH_MODELS]

def cuda_speech_available() -> bool:
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            return False
        if os.name == 'nt':
            ctypes.WinDLL('cublas64_12.dll')
            ctypes.WinDLL('cudnn64_9.dll')
        else:
            if not ctypes.util.find_library('cublas') or not ctypes.util.find_library('cudnn'):
                return False
        return True
    except (OSError, ImportError):
        return False

def validate_config(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('Unknown test setting.')
    config = {**DEFAULTS, **value, 'enabled': True}
    if not isinstance(config['model'],str) or len(config['model']) > 120:
        raise ValueError('Select an installed video model.')
    if config['speech_model'] not in SPEECH_MODELS or config['speech_device'] not in {'cpu','cuda'}:
        raise ValueError('Select a supported speech model and device.')
    if type(config['cpu_threads']) is not int or not 1 <= config['cpu_threads'] <= min(32,os.cpu_count() or 1):
        raise ValueError('Choose a valid number of CPU threads.')
    if type(config['frame_interval_s']) is not int or config['frame_interval_s'] not in {2,5,10}:
        raise ValueError('Frame sampling must be every 2, 5 or 10 seconds.')
    if type(config['frame_width']) is not int or config['frame_width'] not in {480,720,768,960}:
        raise ValueError('Frame width must be 480, 720, 768 or 960 pixels.')
    if config['catch_up'] not in {'complete','live'} or config['vision_detail'] not in {'brief','detailed'}:
        raise ValueError('Choose a valid catch-up and frame description setting.')
    if type(config['model_cpu_threads']) is not int or not 1 <= config['model_cpu_threads'] <= min(32,os.cpu_count() or 1):
        raise ValueError('Choose a valid number of model CPU threads.')
    if config['answer_style'] not in {'natural','quotes'} or type(config['allow_predictions']) is not bool:
        raise ValueError('Select a valid answer style and prediction setting.')
    return config

def read_results(cache: Path) -> list[dict]:
    try:
        result = json.loads((cache/'test-results.json').read_text(encoding='utf-8'))
        return result[-20:] if isinstance(result,list) else []
    except (OSError,ValueError):
        return []

def save_result(cache: Path, result: dict) -> None:
    rows = [row for row in read_results(cache) if row.get('run_id') != result['run_id']]
    temporary = cache/'test-results.tmp'
    temporary.write_text(json.dumps((rows+[result])[-20:],indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(cache/'test-results.json')
