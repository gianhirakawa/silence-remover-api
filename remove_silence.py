#!/usr/bin/env python3
import subprocess
import re
import os
import json
import requests
import uuid

def download_from_url(url, output_path):
    """Download video from URL"""
    print(f"📥 Downloading from URL...")
    
    response = requests.get(url, stream=True, timeout=300)
    response.raise_for_status()
    
    total_size = int(response.headers.get('content-length', 0))
    downloaded = 0
    
    with open(output_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    progress = (downloaded / total_size) * 100
                    print(f"\r  Progress: {progress:.1f}%", end='', flush=True)
    
    print(f"\n✅ Downloaded: {downloaded / (1024*1024):.2f}MB")
    return output_path

def detect_silence(input_file, noise_level="-30dB", min_duration=0.5):
    """Detect silent parts in video"""
    print(f"🔍 Detecting silence...")
    print(f"   Threshold: {noise_level}, Min duration: {min_duration}s")
    
    cmd = [
        'ffmpeg',
        '-i', input_file,
        '-af', f'silencedetect=noise={noise_level}:d={min_duration}',
        '-f', 'null',
        '-'
    ]
    
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output = result.stdout
    
    silence_starts = re.findall(r'silence_start: ([\d.]+)', output)
    silence_ends = re.findall(r'silence_end: ([\d.]+)', output)
    
    silences = []
    for start, end in zip(silence_starts, silence_ends):
        silences.append({
            'start': float(start),
            'end': float(end),
            'duration': float(end) - float(start)
        })
    
    print(f"✅ Found {len(silences)} silent periods")
    return silences

def get_video_duration(input_file):
    """Get total video duration"""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'json',
        input_file
    ]
    
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    data = json.loads(result.stdout)
    return float(data['format']['duration'])

def create_filter_complex(silences, video_duration):
    """Create ffmpeg filter to remove silence"""
    
    segments = []
    current_time = 0
    
    for silence in silences:
        if silence['start'] > current_time:
            segments.append({
                'start': current_time,
                'end': silence['start']
            })
        current_time = silence['end']
    
    if current_time < video_duration:
        segments.append({
            'start': current_time,
            'end': video_duration
        })
    
    if not segments:
        print("⚠️  No segments to keep!")
        return None
    
    print(f"✂️  Keeping {len(segments)} segments:")
    for i, seg in enumerate(segments):
        duration = seg['end'] - seg['start']
        print(f"   Segment {i+1}: {seg['start']:.2f}s - {seg['end']:.2f}s (duration: {duration:.2f}s)")
    
    filter_parts = []
    
    for i, seg in enumerate(segments):
        filter_parts.append(
            f"[0:v]trim=start={seg['start']}:end={seg['end']},setpts=PTS-STARTPTS[v{i}]"
        )
        filter_parts.append(
            f"[0:a]atrim=start={seg['start']}:end={seg['end']},asetpts=PTS-STARTPTS[a{i}]"
        )
    
    inputs = ''.join([f"[v{i}][a{i}]" for i in range(len(segments))])
    filter_parts.append(
        f"{inputs}concat=n={len(segments)}:v=1:a=1[outv][outa]"
    )
    
    return '; '.join(filter_parts)

def remove_silence_from_url(video_url, noise_level="-30dB", min_duration=0.5):
    """Main function - accepts URL input"""
    
    job_id = str(uuid.uuid4())[:8]
    temp_dir = "/tmp/videos"
    os.makedirs(temp_dir, exist_ok=True)
    
    input_path = os.path.join(temp_dir, f"{job_id}_input.mp4")
    output_path = os.path.join(temp_dir, f"{job_id}_output.mp4")
    
    try:
        # Download
        download_from_url(video_url, input_path)
        
        # Detect silences
        silences = detect_silence(input_path, noise_level, min_duration)
        
        if not silences:
            print("✅ No silence detected!")
            return {
                'status': 'no_silence',
                'output_path': input_path,
                'message': 'No silence found'
            }
        
        # Get video info
        print("\n📹 Getting video info...")
        video_duration = get_video_duration(input_path)
        print(f"📹 Video duration: {video_duration:.2f}s")
        
        silence_duration = sum([s['duration'] for s in silences])
        print(f"🤫 Total silence: {silence_duration:.2f}s ({silence_duration/video_duration*100:.1f}%)")
        
        # Create filter
        filter_complex = create_filter_complex(silences, video_duration)
        
        if not filter_complex:
            return {'status': 'error', 'message': 'Could not create filter'}
        
        # Process video
        print(f"\n✂️  Removing silence...")
        
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            '-map', '[outa]',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '192k',
            output_path,
            '-y'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"\n✅ Processing complete!")
            
            input_size = os.path.getsize(input_path) / (1024 * 1024)
            output_size = os.path.getsize(output_path) / (1024 * 1024)
            print(f"📊 Input: {input_size:.2f}MB → Output: {output_size:.2f}MB")
            
            return {
                'status': 'success',
                'output_path': output_path,
                'silence_removed': len(silences),
                'time_saved_seconds': silence_duration,
                'input_size_mb': input_size,
                'output_size_mb': output_size
            }
        else:
            print(f"❌ FFmpeg error: {result.stderr}")
            return {'status': 'error', 'message': 'FFmpeg processing failed', 'error': result.stderr}
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return {'status': 'error', 'message': str(e)}
    
    finally:
        # Cleanup input file (keep output for download)
        if os.path.exists(input_path):
            os.remove(input_path)
            print(f"🧹 Cleaned up input file")