# Silence Remover API

Docker-based API for removing silence from videos using FFmpeg.

## Quick Start

### Build and Run
```bash
docker-compose up --build
```

### Test
```bash
curl http://localhost:5000/health
```

## API Endpoints

### POST /remove-silence
Remove silence from video and download result.

**Request:**
```json
{
  "video_url": "https://storage.googleapis.com/bucket/video.mp4",
  "noise_level": "-30dB",
  "min_duration": 0.5
}
```

**Response:** Binary video file (MP4)

### POST /remove-silence/info
Get silence detection info without processing.

**Response:**
```json
{
  "status": "success",
  "video_duration": 120.5,
  "silence_periods": 5,
  "total_silence_duration": 15.3,
  "silence_percentage": 12.7
}
```

## Development

### Local Testing
```bash
python app.py
```

### Docker Build
```bash
docker build -t silence-remover .
docker run -p 5000:5000 silence-remover
```

## Production Deployment

See deployment guides for:
- AWS ECS
- Google Cloud Run
- DigitalOcean
- Render.com

## New Endpoints

### POST /burn-captions
Burn captions into video from ElevenLabs transcript.

**Request:**
```json
{
  "video_url": "https://...",
  "words": [...],
  "words_per_line": 5,
  "caption_style": "grouped",
  "style": {
    "font_size": 24,
    "color": "white",
    "outline": true
  }
}
```

**Response:** Binary video file with captions

### POST /create-srt
Generate SRT file from words (no video processing).

**Request:**
```json
{
  "words": [...],
  "words_per_line": 5
}
```

**Response:** SRT file