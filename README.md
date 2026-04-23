# Video Subtitle Generator

A Python script that automatically generates Greek and English subtitles from video files using AI-powered speech recognition and translation.

## Features

- **Automatic Language Detection**: Detects whether audio is in Greek or English
- **Speech-to-Text**: Uses OpenAI Whisper for accurate transcription
- **Translation**: Translates between Greek and English using Google Translate
- **Dialect Correction**: Fixes Cypriot Greek dialect words to standard Greek
- **Video Processing**: Supports multiple video formats (.mp4, .mkv, .avi, .mov, etc.)
- **Chunking**: Automatically splits long videos for better accuracy
- **Hardware Adaptive**: Adjusts processing mode based on CPU/GPU capabilities
- **Error Filtering**: Removes hallucinations, silence, watermarks, and repeated text
- **SRT Output**: Generates properly formatted subtitle files

## Requirements

- Python 3.8+
- FFmpeg (place in project root or install system-wide)
- Required Python packages:
  - `openai-whisper`
  - `torch`
  - `deep-translator`
  - `argostranslate` (optional, for offline translation)

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/video-subtitle-generator.git
   cd video-subtitle-generator
   ```

2. Create a virtual environment (recommended):
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install openai-whisper torch deep-translator
   ```

4. (Optional) For offline translation, run the setup script:
   ```bash
   python setup_language_pack.py
   ```

5. Ensure FFmpeg is available:
   - Download FFmpeg and place `ffmpeg.exe` in the project root, or
   - Install FFmpeg system-wide and ensure it's in your PATH

## Usage

1. Place video files in the `input/` folder
2. Run the script:
   ```bash
   python translation_script.py
   ```

The script will:
- Process each video in `input/`
- Generate Greek and English subtitle files in `output/`
- Move processed videos to `translated_videos/`
- Save detailed logs in `logs/`

### Command Line Options

- `--mode`: Processing mode (`auto-hw`, `auto`, `fast`, `best`) - default: `auto-hw`
- `--model`: Whisper model override (`small`, `medium`, `large-v3`) - default: auto-selected
- `--split-threshold-min`: Auto-split threshold in minutes - default: 20.0
- `--chunk-min`: Chunk size in minutes - default: 15.0
- `--keep-temp`: Keep temporary WAV files for debugging

Example:
```bash
python translation_script.py --mode fast --model small
```

## How It Works

1. **Audio Extraction**: Extracts audio from video using FFmpeg
2. **Language Detection**: Uses Whisper to detect spoken language
3. **Transcription**: Transcribes audio to text in the detected language
4. **Translation**: Translates to the other language using Google Translate
5. **Post-processing**: Applies dialect corrections and filters out errors
6. **SRT Generation**: Creates properly timed subtitle files

## Project Structure

```
.
├── input/                 # Place video files here
├── output/                # Generated subtitle files (.srt)
├── translated_videos/     # Processed videos moved here
├── logs/                  # Processing logs
├── translation_script.py  # Main script
├── setup_language_pack.py # Offline translation setup
├── ffmpeg.exe            # FFmpeg binary (optional)
└── README.md             # This file
```

## Supported Video Formats

- MP4 (.mp4)
- MKV (.mkv)
- AVI (.avi)
- MOV (.mov)
- M4V (.m4v)
- WMV (.wmv)
- FLV (.flv)
- WebM (.webm)
- MPEG (.mpeg, .mpg)

## Troubleshooting

- **FFmpeg not found**: Place `ffmpeg.exe` in project root or install system-wide
- **CUDA errors**: If GPU issues occur, the script will fall back to CPU
- **Translation failures**: Check internet connection for Google Translate
- **Memory issues**: Use `--mode fast` for large videos or reduce chunk size

## License

[MIT License](LICENSE)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Credits

- [OpenAI Whisper](https://github.com/openai/whisper) for speech recognition
- [Deep Translator](https://github.com/nidhaloff/deep-translator) for translation
- [FFmpeg](https://ffmpeg.org/) for audio/video processing