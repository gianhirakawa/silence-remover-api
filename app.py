from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import time
import uuid
import subprocess
import threading
import requests
from remove_silence import remove_silence_from_url, download_from_url
from create_srt import create_srt_from_words, create_word_by_word_srt

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# --- NEW: Local Fonts Configuration ---
# Define the path to your local fonts folder next to app.py
FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
# Ensure the directory exists (you still need to upload files manually)
os.makedirs(FONTS_DIR, exist_ok=True)

STYLE_PRESETS = {
    'default': {
        'font_size': 24,
        'font_name': 'DejaVu Sans', # Safe default for Linux/Render
        'color': 'white',
        'outline': True,
        'spacing': 0,
        'margin_v': 70
    },
    'tiktok': {
        'font_size': 48,
        'font_name': 'Montserrat Black', # Make sure Montserrat-Black.otf is in /fonts
        'color': 'white',
        'outline': True,
        'spacing': -1.5, # Tighten letters for blocky look
        'margin_v': 85   # Higher up (chest level)
    },
    'youtube': {
        'font_size': 32,
        'font_name': 'DejaVu Sans',
        'color': 'white',
        'outline': True,
        'spacing': 0,
        'margin_v': 50
    },
    'minimal': {
        'font_size': 20,
        'font_name': 'DejaVu Sans Mono',
        'color': 'white',
        'outline': False,
        'spacing': 0,
        'margin_v': 50
    },
    'hormozi': {
        'font_size': 24,
        'font_name': 'Montserrat Black', # Make sure Montserrat-Black.otf is in /fonts
        'color': 'white',
        'bold': True,
        'outline': 3,
        'shadow': 2,
        'spacing': -1.0,
        'margin_v': 80
    }
}

# Store processing results temporarily
processing_jobs = {}
# Lock for thread-safe access to processing_jobs
jobs_lock = threading.Lock()

def validate_video_url(video_url):
    """Validate that video_url is not pointing to our own API endpoints"""
    if not video_url:
        return False, "video_url is required"
    
    # Check if URL points to our own API endpoints
    api_endpoints = [
        '/remove-silence/download/',
        '/burn-captions/download/',
        '/remove-silence/status/',
        '/burn-captions/status/',
        '/remove-silence',
        '/burn-captions'
    ]
    
    for endpoint in api_endpoints:
        if endpoint in video_url:
            return False, f"video_url cannot point to API endpoints. Please provide a direct video URL, not '{endpoint}'. If you need to use a processed video, download it first and upload it to a storage service."
    
    return True, None

def process_silence_removal(job_id, video_url, noise_level, min_duration):
    """Background function to process video removal"""
    with jobs_lock:
        # Update existing job instead of overwriting
        if job_id in processing_jobs:
            processing_jobs[job_id].update({
                'status': 'processing',
                'progress': 'Downloading video...'
            })
        else:
            processing_jobs[job_id] = {
                'status': 'processing',
                'created_at': time.time(),
                'video_url': video_url,
                'progress': 'Downloading video...',
                'job_type': 'remove-silence'
            }
    
    try:
        print(f"📝 [Job {job_id}] Processing request for: {video_url}")
        
        # Update progress
        with jobs_lock:
            if job_id in processing_jobs:
                processing_jobs[job_id]['progress'] = 'Processing video...'
        
        # Process video
        result = remove_silence_from_url(
            video_url,
            noise_level=noise_level,
            min_duration=min_duration
        )
        
        # Update job status
        with jobs_lock:
            if job_id in processing_jobs:
                if result['status'] == 'success':
                    processing_jobs[job_id].update({
                        'status': 'completed',
                        'output_path': result['output_path'],
                        'silence_removed': result.get('silence_removed', 0),
                        'time_saved_seconds': result.get('time_saved_seconds', 0),
                        'input_size_mb': result.get('input_size_mb', 0),
                        'output_size_mb': result.get('output_size_mb', 0),
                        'completed_at': time.time()
                    })
                elif result['status'] == 'no_silence':
                    processing_jobs[job_id].update({
                        'status': 'no_silence',
                        'message': result.get('message', 'No silence found'),
                        'completed_at': time.time()
                    })
                else:
                    processing_jobs[job_id].update({
                        'status': 'error',
                        'error': result.get('message', 'Unknown error'),
                        'completed_at': time.time()
                    })
    except Exception as e:
        print(f"❌ [Job {job_id}] Error: {e}")
        with jobs_lock:
            if job_id in processing_jobs:
                processing_jobs[job_id].update({
                    'status': 'error',
                    'error': str(e),
                    'completed_at': time.time()
                })

def process_burn_captions(job_id, video_url, words, words_per_line, caption_style, all_caps, style_preset, style):
    """Background function to process caption burning"""
    import json as json_lib
    
    with jobs_lock:
        # Update existing job instead of overwriting
        if job_id in processing_jobs:
            processing_jobs[job_id].update({
                'status': 'processing',
                'progress': 'Downloading video...'
            })
        else:
            processing_jobs[job_id] = {
                'status': 'processing',
                'created_at': time.time(),
                'video_url': video_url,
                'progress': 'Downloading video...',
                'job_type': 'burn-captions'
            }
    
    try:
        print(f"📝 [Job {job_id}] Processing burn-captions request")
        
        # Sanitize words
        if isinstance(words, list):
            for word_obj in words:
                if isinstance(word_obj, dict) and 'word' in word_obj:
                    word_obj['word'] = " ".join(word_obj['word'].split())
        
        # Update progress
        with jobs_lock:
            if job_id in processing_jobs:
                processing_jobs[job_id]['progress'] = 'Creating SRT file...'
        
        temp_dir = "/tmp/videos"
        os.makedirs(temp_dir, exist_ok=True)
        
        input_path = os.path.join(temp_dir, f"{job_id}_input.mp4")
        srt_path = os.path.join(temp_dir, f"{job_id}_subs.srt")
        output_path = os.path.join(temp_dir, f"{job_id}_output.mp4")
        
        # Download video
        print(f"📥 Downloading video for job {job_id}...")
        try:
            download_from_url(video_url, input_path)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_msg = f"Video URL returned 404. The URL may be expired or invalid. If you're trying to use a processed video from this API, please download it first and upload it to a storage service (e.g., Google Cloud Storage, S3, or a temporary file hosting service)."
            else:
                error_msg = f"Failed to download video: {str(e)}"
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Failed to download video from URL: {str(e)}. Please ensure the URL points to a direct video file, not an API endpoint."
            raise Exception(error_msg)
        
        # Update progress
        with jobs_lock:
            if job_id in processing_jobs:
                processing_jobs[job_id]['progress'] = 'Burning captions...'
        
        # Create SRT
        print(f"📝 Creating SRT file... ({len(words)} words, {words_per_line} per line, all_caps={all_caps})")
        if caption_style == 'word-by-word':
            subtitle_count = create_word_by_word_srt(words, srt_path, all_caps=all_caps)
        else:
            subtitle_count = create_srt_from_words(words, srt_path, words_per_line, all_caps=all_caps)
        
        # Apply Thin Space Patch
        try:
            with open(srt_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            new_lines = []
            for line in lines:
                if '-->' in line or (line.strip().isdigit() and len(line.strip()) < 5):
                    new_lines.append(line)
                else:
                    new_lines.append(line.replace(' ', '\u2009'))
            
            with open(srt_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            print("✨ Applied Thin Space Patch (U+2009) to SRT")
        except Exception as e:
            print(f"⚠️ Could not apply thin space patch: {e}")
        
        # Build style string
        font_name = style.get('font_name', 'DejaVu Sans')
        font_size = int(style.get('font_size', 24))
        color = style.get('color', 'white')
        outline = style.get('outline', True)
        margin_h = int(style.get('margin_horizontal', 40))
        margin_v = int(style.get('margin_vertical', style.get('margin_v', 70)))
        spacing = float(style.get('spacing', -1.0))
        shadow = int(style.get('shadow', 0))
        
        color_map = {
            'white': '&H00FFFFFF', 'black': '&H00000000',
            'yellow': '&H0000FFFF', 'red': '&H000000FF',
            'green': '&H0000FF00', 'blue': '&H00FF0000'
        }
        primary_color = color_map.get(str(color).lower(), '&H00FFFFFF')
        
        style_string = f"FontName={font_name},FontSize={font_size},PrimaryColour={primary_color},MarginV={margin_v},MarginL={margin_h},MarginR={margin_h},Alignment=2,Spacing={spacing},Shadow={shadow}"
        
        if outline:
            outline_width = style.get('outline_width', 2)
            if isinstance(outline, (int, float)):
                outline_width = outline
            style_string += f",OutlineColour=&H00000000,BorderStyle=1,Outline={outline_width}"
        else:
            style_string += ",BorderStyle=1,Outline=0"
        
        # Burn subtitles
        print(f"🔥 Burning captions using font: {font_name}")
        vf_string = f"subtitles={srt_path}:fontsdir={FONTS_DIR}:force_style='{style_string}'"
        
        cmd = [
            'ffmpeg', '-i', input_path,
            '-vf', vf_string,
            '-c:a', 'copy',
            output_path, '-y'
        ]

        # Don't buffer FFmpeg output - stream and keep only last 2KB for errors
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )
        stderr_buffer = []
        max_stderr_size = 2048
        for line in process.stderr:
            stderr_buffer.append(line)
            while sum(len(l) for l in stderr_buffer) > max_stderr_size:
                stderr_buffer.pop(0)
        returncode = process.wait()
        stderr_output = ''.join(stderr_buffer)

        # Clean up input and SRT files
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(srt_path):
            os.remove(srt_path)

        if returncode == 0:
            print("✅ Captions burned successfully!")
            input_size = os.path.getsize(output_path) / (1024 * 1024) if os.path.exists(output_path) else 0
            
            with jobs_lock:
                if job_id in processing_jobs:
                    processing_jobs[job_id].update({
                        'status': 'completed',
                        'output_path': output_path,
                        'output_size_mb': input_size,
                        'completed_at': time.time()
                    })
        else:
            print(f"❌ FFmpeg error: {stderr_output}")
            with jobs_lock:
                if job_id in processing_jobs:
                    processing_jobs[job_id].update({
                        'status': 'error',
                        'error': f'Failed to burn captions: {stderr_output[:200]}',
                        'completed_at': time.time()
                    })
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ [Job {job_id}] Error: {e}")
        with jobs_lock:
            if job_id in processing_jobs:
                processing_jobs[job_id].update({
                    'status': 'error',
                    'error': str(e),
                    'completed_at': time.time()
                })

def cleanup_old_jobs():
    """Remove jobs older than 15 minutes and enforce max job limit"""
    current_time = time.time()
    max_jobs = 50  # Limit total jobs to prevent unbounded memory growth

    with jobs_lock:
        to_remove = []
        for job_id, job in processing_jobs.items():
            # Clean up completed/error jobs older than 15 minutes (was 1 hour)
            if job.get('status') in ['completed', 'error', 'no_silence']:
                completed_at = job.get('completed_at', job.get('created_at', 0))
                if current_time - completed_at > 900:  # 15 minutes
                    # Also delete the output file if it exists
                    output_path = job.get('output_path')
                    if output_path and os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                            print(f"🧹 Cleaned up old job {job_id} and file")
                        except:
                            pass
                    to_remove.append(job_id)

        # If still over limit, remove oldest completed jobs first
        remaining = len(processing_jobs) - len(to_remove)
        if remaining > max_jobs:
            # Sort completed jobs by completion time, oldest first
            completed_jobs = [
                (jid, j.get('completed_at', j.get('created_at', 0)))
                for jid, j in processing_jobs.items()
                if j.get('status') in ['completed', 'error', 'no_silence'] and jid not in to_remove
            ]
            completed_jobs.sort(key=lambda x: x[1])
            # Remove oldest until under limit
            for jid, _ in completed_jobs[:remaining - max_jobs]:
                output_path = processing_jobs[jid].get('output_path')
                if output_path and os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except:
                        pass
                to_remove.append(jid)

        for job_id in to_remove:
            del processing_jobs[job_id]

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'silence-remover-api',
        'version': '1.0.0'
    })

@app.route('/fonts', methods=['GET'])
def list_fonts():
    """
    List all available fonts on the system that FFmpeg can use.
    """
    try:
        # Run fc-list to get font families - limit output to prevent memory issues
        process = subprocess.Popen(
            ['fc-list', ':', 'family'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        fonts = set()
        for line in process.stdout:
            if line.strip():
                families = line.split(',')
                for family in families:
                    fonts.add(family.strip())

        _, stderr = process.communicate()
        if process.returncode != 0:
            return jsonify({
                'status': 'error',
                'message': 'Failed to list fonts',
                'details': stderr
            }), 500

        sorted_fonts = sorted(list(fonts))
        
        # Check for local fonts in the fonts/ folder
        local_fonts = []
        if os.path.exists(FONTS_DIR):
            local_fonts = [f for f in os.listdir(FONTS_DIR) if f.endswith(('.ttf', '.otf'))]
        
        return jsonify({
            'status': 'success',
            'count': len(sorted_fonts),
            'system_os': os.name,
            'local_fonts_dir': FONTS_DIR,
            'local_fonts_found': local_fonts,
            'system_fonts': sorted_fonts
        })
        
    except FileNotFoundError:
        return jsonify({
            'status': 'error',
            'message': 'Font utility (fc-list) not found. If on Linux, install fontconfig.'
        }), 404
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/remove-silence', methods=['POST'])
def remove_silence_async():
    """Async silence removal - returns job ID immediately"""
    data = request.json
    video_url = data.get('video_url')
    if not video_url:
        return jsonify({'error': 'video_url required'}), 400
    
    # Validate video URL
    is_valid, error_msg = validate_video_url(video_url)
    if not is_valid:
        return jsonify({'error': error_msg}), 400
    
    noise_level = data.get('noise_level', '-30dB')
    min_duration = float(data.get('min_duration', 0.5))
    
    # Generate job ID
    job_id = str(uuid.uuid4())
    
    # Initialize job
    with jobs_lock:
        processing_jobs[job_id] = {
            'status': 'pending',
            'created_at': time.time(),
            'video_url': video_url,
            'noise_level': noise_level,
            'min_duration': min_duration,
            'job_type': 'remove-silence'
        }
    
    # Start background processing
    thread = threading.Thread(
        target=process_silence_removal,
        args=(job_id, video_url, noise_level, min_duration)
    )
    thread.daemon = True
    thread.start()
    
    # Clean up old jobs periodically
    cleanup_old_jobs()
    
    return jsonify({
        'status': 'pending',
        'job_id': job_id,
        'message': 'Processing started. Use /remove-silence/status/{job_id} to check status.'
    }), 202

@app.route('/remove-silence/status/<job_id>', methods=['GET'])
def remove_silence_status(job_id):
    """Check status of a silence removal job"""
    cleanup_old_jobs()
    
    with jobs_lock:
        job = processing_jobs.get(job_id)
    
    if not job:
        return jsonify({
            'status': 'error',
            'message': 'Job not found. Job may have expired or never existed.'
        }), 404
    
    # Return job status (without sensitive paths)
    response = {
        'job_id': job_id,
        'status': job['status'],
        'created_at': job.get('created_at'),
        'progress': job.get('progress', 'Unknown')
    }
    
    if job['status'] == 'completed':
        response.update({
            'silence_removed': job.get('silence_removed', 0),
            'time_saved_seconds': job.get('time_saved_seconds', 0),
            'input_size_mb': job.get('input_size_mb', 0),
            'output_size_mb': job.get('output_size_mb', 0),
            'completed_at': job.get('completed_at'),
            'download_url': f'/remove-silence/download/{job_id}'
        })
    elif job['status'] == 'no_silence':
        response.update({
            'message': job.get('message', 'No silence found'),
            'completed_at': job.get('completed_at')
        })
    elif job['status'] == 'error':
        response.update({
            'error': job.get('error', 'Unknown error'),
            'completed_at': job.get('completed_at')
        })
    
    return jsonify(response), 200

@app.route('/remove-silence/download/<job_id>', methods=['GET'])
def remove_silence_download(job_id):
    """Download the processed video (streaming for large files)"""
    with jobs_lock:
        job = processing_jobs.get(job_id)
    
    if not job:
        return jsonify({
            'status': 'error',
            'message': 'Job not found'
        }), 404
    
    if job.get('job_type') != 'remove-silence':
        actual_type = job.get('job_type', 'unknown')
        return jsonify({
            'status': 'error',
            'message': f'Job ID does not match remove-silence job type. This job is of type: {actual_type}. Use /{actual_type}/download/{job_id} instead.'
        }), 400
    
    if job['status'] != 'completed':
        return jsonify({
            'status': 'error',
            'message': f'Job is not completed. Current status: {job["status"]}'
        }), 400
    
    output_path = job.get('output_path')
    if not output_path or not os.path.exists(output_path):
        return jsonify({
            'status': 'error',
            'message': 'Output file not found. It may have been cleaned up.'
        }), 404
    
    # Use streaming for large files to avoid timeout
    def generate():
        with open(output_path, 'rb') as f:
            while True:
                chunk = f.read(256 * 1024)  # 256KB chunks (was 8KB)
                if not chunk:
                    break
                yield chunk

    from flask import Response
    response = Response(
        generate(),
        mimetype='video/mp4',
        headers={
            'Content-Disposition': 'attachment; filename=cleaned_video.mp4',
            'Content-Length': str(os.path.getsize(output_path))
        }
    )

    @response.call_on_close
    def cleanup():
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
                print(f"🧹 Cleaned up downloaded file for job {job_id}")
            except:
                pass

    return response

@app.route('/remove-silence/info', methods=['POST'])
def remove_silence_info():
    """Get silence information without processing"""
    data = request.json
    video_url = data.get('video_url')
    if not video_url:
        return jsonify({'error': 'video_url required'}), 400
    
    noise_level = data.get('noise_level', '-30dB')
    min_duration = float(data.get('min_duration', 0.5))
    
    try:
        from remove_silence import download_from_url, detect_silence, get_video_duration
        job_id = str(uuid.uuid4())[:8]
        temp_path = f"/tmp/videos/{job_id}_temp.mp4"
        
        download_from_url(video_url, temp_path)
        silences = detect_silence(temp_path, noise_level, min_duration)
        duration = get_video_duration(temp_path)
        
        os.remove(temp_path)
        total_silence = sum([s['duration'] for s in silences])
        
        return jsonify({
            'status': 'success',
            'video_duration': duration,
            'silence_periods': len(silences),
            'total_silence_duration': total_silence,
            'silence_percentage': (total_silence / duration * 100) if duration > 0 else 0,
            'silences': silences
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/burn-captions', methods=['POST'])
def burn_captions_async():
    """
    Async burn captions - returns job ID immediately
    
    POST Body:
    {
        "video_url": "https://...",
        "words": [...], 
        "words_per_line": 5,
        "caption_style": "grouped" | "word-by-word",
        "all_caps": true | false,
        "style_preset": "tiktok",
        "style": {
            "font_name": "Montserrat Black",
            "font_size": 24,
            "margin_horizontal": 40,
            "margin_vertical": 70,
            "spacing": -1.5,
            "color": "white"
        }
    }
    """
    import json as json_lib
    
    data = request.json
    video_url = data.get('video_url')
    words_input = data.get('words')
    
    if not video_url or not words_input:
        return jsonify({'error': 'video_url and words required'}), 400
    
    # Validate video URL
    is_valid, error_msg = validate_video_url(video_url)
    if not is_valid:
        return jsonify({'error': error_msg}), 400
    
    try:
        # Handle words
        if isinstance(words_input, str):
            words = json_lib.loads(words_input)
        elif isinstance(words_input, list):
            words = words_input
        else:
            return jsonify({'error': 'words must be array or JSON string'}), 400
        
        # Handle options
        words_per_line = int(data.get('words_per_line', 5))
        caption_style = data.get('caption_style', 'grouped')
        
        all_caps = data.get('all_caps', False)
        if isinstance(all_caps, str):
            all_caps = all_caps.lower() in ['true', '1', 'yes']
        
        # Handle style
        style_input = data.get('style', {})
        if isinstance(style_input, str):
            try: style = json_lib.loads(style_input)
            except: style = {}
        else:
            style = style_input if style_input else {}
        
        # Handle style preset
        style_preset = data.get('style_preset')
        if style_preset and style_preset in STYLE_PRESETS:
            preset = STYLE_PRESETS[style_preset].copy()
            preset.update(style)
            style = preset
        
        # Generate job ID
        job_id = str(uuid.uuid4())
        
        # Initialize job
        with jobs_lock:
            processing_jobs[job_id] = {
                'status': 'pending',
                'created_at': time.time(),
                'video_url': video_url,
                'job_type': 'burn-captions'
            }
        
        # Start background processing
        thread = threading.Thread(
            target=process_burn_captions,
            args=(job_id, video_url, words, words_per_line, caption_style, all_caps, style_preset, style)
        )
        thread.daemon = True
        thread.start()
        
        # Clean up old jobs periodically
        cleanup_old_jobs()
        
        return jsonify({
            'status': 'pending',
            'job_id': job_id,
            'message': 'Processing started. Use /burn-captions/status/{job_id} to check status.'
        }), 202
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/burn-captions/status/<job_id>', methods=['GET'])
def burn_captions_status(job_id):
    """Check status of a burn-captions job"""
    cleanup_old_jobs()
    
    with jobs_lock:
        job = processing_jobs.get(job_id)
    
    if not job:
        return jsonify({
            'status': 'error',
            'message': 'Job not found. Job may have expired or never existed.'
        }), 404
    
    if job.get('job_type') != 'burn-captions':
        return jsonify({
            'status': 'error',
            'message': 'Job ID does not match burn-captions job type'
        }), 400
    
    # Return job status
    response = {
        'job_id': job_id,
        'status': job['status'],
        'created_at': job.get('created_at'),
        'progress': job.get('progress', 'Unknown')
    }
    
    if job['status'] == 'completed':
        response.update({
            'output_size_mb': job.get('output_size_mb', 0),
            'completed_at': job.get('completed_at'),
            'download_url': f'/burn-captions/download/{job_id}'
        })
    elif job['status'] == 'error':
        response.update({
            'error': job.get('error', 'Unknown error'),
            'completed_at': job.get('completed_at')
        })
    
    return jsonify(response), 200

@app.route('/burn-captions/download/<job_id>', methods=['GET'])
def burn_captions_download(job_id):
    """Download the processed video with captions"""
    with jobs_lock:
        job = processing_jobs.get(job_id)
    
    if not job:
        return jsonify({
            'status': 'error',
            'message': 'Job not found'
        }), 404
    
    if job.get('job_type') != 'burn-captions':
        actual_type = job.get('job_type', 'unknown')
        return jsonify({
            'status': 'error',
            'message': f'Job ID does not match burn-captions job type. This job is of type: {actual_type}. Use /{actual_type}/download/{job_id} instead.'
        }), 400
    
    if job['status'] != 'completed':
        return jsonify({
            'status': 'error',
            'message': f'Job is not completed. Current status: {job["status"]}'
        }), 400
    
    output_path = job.get('output_path')
    if not output_path or not os.path.exists(output_path):
        return jsonify({
            'status': 'error',
            'message': 'Output file not found. It may have been cleaned up.'
        }), 404
    
    # Use streaming for large files
    def generate():
        with open(output_path, 'rb') as f:
            while True:
                chunk = f.read(256 * 1024)  # 256KB chunks (was 8KB)
                if not chunk:
                    break
                yield chunk

    from flask import Response
    response = Response(
        generate(),
        mimetype='video/mp4',
        headers={
            'Content-Disposition': f'attachment; filename=captioned_video.mp4',
            'Content-Length': str(os.path.getsize(output_path))
        }
    )

    @response.call_on_close
    def cleanup():
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
                print(f"🧹 Cleaned up downloaded file for job {job_id}")
            except:
                pass

    return response

@app.route('/create-srt', methods=['POST'])
def create_srt_only():
    """
    Just create SRT file from words (no video processing)
    
    POST Body:
    {
        "words": [...],
        "words_per_line": 5,
        "caption_style": "grouped"
    }
    
    Returns: SRT file
    """
    data = request.json
    words = data.get('words')
    if not words: return jsonify({'error': 'words required'}), 400
    
    words_per_line = data.get('words_per_line', 5)
    caption_style = data.get('caption_style', 'grouped')
    
    try:
        job_id = str(uuid.uuid4())[:8]
        srt_path = f"/tmp/videos/{job_id}_subs.srt"
        
        if caption_style == 'word-by-word':
            create_word_by_word_srt(words, srt_path)
        else:
            create_srt_from_words(words, srt_path, words_per_line)
        
        response = send_file(srt_path, mimetype='text/plain', as_attachment=True, download_name='subtitles.srt')
        @response.call_on_close
        def cleanup():
            import time
            time.sleep(1)
            if os.path.exists(srt_path): os.remove(srt_path)
        return response
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test', methods=['GET'])
def test():
    """Test endpoint"""
    return jsonify({
        'message': 'API is working!',
        'endpoints': {
            'health': '/health (GET)',
            'fonts': '/fonts (GET)',
            'remove_silence_async': '/remove-silence (POST) - Returns job ID',
            'remove_silence_status': '/remove-silence/status/<job_id> (GET)',
            'remove_silence_download': '/remove-silence/download/<job_id> (GET)',
            'get_info': '/remove-silence/info (POST)',
            'burn_captions_async': '/burn-captions (POST) - Returns job ID',
            'burn_captions_status': '/burn-captions/status/<job_id> (GET)',
            'burn_captions_download': '/burn-captions/download/<job_id> (GET)',
            'create_srt': '/create-srt (POST)' 
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)