from flask import Flask, request, jsonify, send_file
import os
import time
import uuid
import subprocess
from remove_silence import remove_silence_from_url, download_from_url
from create_srt import create_srt_from_words, create_word_by_word_srt

app = Flask(__name__)

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
        # Run fc-list to get font families
        result = subprocess.run(['fc-list', ':', 'family'], capture_output=True, text=True)
        
        if result.returncode != 0:
            return jsonify({
                'status': 'error',
                'message': 'Failed to list fonts',
                'details': result.stderr
            }), 500
            
        # Parse the output
        fonts = set()
        raw_output = result.stdout.strip().split('\n')
        
        for line in raw_output:
            if line.strip():
                families = line.split(',')
                for family in families:
                    fonts.add(family.strip())
        
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
def remove_silence_sync():
    """Synchronous silence removal"""
    data = request.json
    video_url = data.get('video_url')
    if not video_url:
        return jsonify({'error': 'video_url required'}), 400
    
    noise_level = data.get('noise_level', '-30dB')
    min_duration = float(data.get('min_duration', 0.5))
    
    print(f"📝 Processing request for: {video_url}")
    
    try:
        result = remove_silence_from_url(
            video_url,
            noise_level=noise_level,
            min_duration=min_duration
        )
        
        if result['status'] == 'success':
            output_path = result['output_path']
            response = send_file(
                output_path,
                mimetype='video/mp4',
                as_attachment=True,
                download_name='cleaned_video.mp4'
            )
            @response.call_on_close
            def cleanup():
                time.sleep(2)
                if os.path.exists(output_path):
                    os.remove(output_path)
            return response
        elif result['status'] == 'no_silence':
            return jsonify({'status': 'no_silence', 'message': result['message']}), 200
        else:
            return jsonify(result), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

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
def burn_captions():
    """
    Burn captions into video from ElevenLabs word timestamps
    
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
    
    try:
        # Handle words
        if isinstance(words_input, str):
            words = json_lib.loads(words_input)
        elif isinstance(words_input, list):
            words = words_input
        else:
            return jsonify({'error': 'words must be array or JSON string'}), 400
            
        # --- NEW: Sanitize Words (Fix double spaces) ---
        # This fixes gaps like "THE  CALLS" -> "THE CALLS"
        if isinstance(words, list):
            for word_obj in words:
                if isinstance(word_obj, dict) and 'word' in word_obj:
                    # Remove double spaces and strip surrounding whitespace
                    word_obj['word'] = " ".join(word_obj['word'].split())
        
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
        
        job_id = str(uuid.uuid4())[:8]
        temp_dir = "/tmp/videos"
        os.makedirs(temp_dir, exist_ok=True)
        
        input_path = os.path.join(temp_dir, f"{job_id}_input.mp4")
        srt_path = os.path.join(temp_dir, f"{job_id}_subs.srt")
        output_path = os.path.join(temp_dir, f"{job_id}_output.mp4")
        
        # Download video
        print(f"📥 Downloading video for job {job_id}...")
        download_from_url(video_url, input_path)
        
        # Create SRT
        print(f"📝 Creating SRT file... ({len(words)} words, {words_per_line} per line, all_caps={all_caps})")
        if caption_style == 'word-by-word':
            subtitle_count = create_word_by_word_srt(words, srt_path, all_caps=all_caps)
        else:
            subtitle_count = create_srt_from_words(words, srt_path, words_per_line, all_caps=all_caps)
        
        # --- NEW: Apply "Thin Space" Patch ---
        # Replace standard spaces with Unicode Thin Spaces (U+2009) in text lines
        # This fixes wide gaps between words when using wide/bold fonts
        try:
            with open(srt_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            new_lines = []
            for line in lines:
                # Don't touch lines that look like timestamps or indices
                if '-->' in line or (line.strip().isdigit() and len(line.strip()) < 5):
                    new_lines.append(line)
                else:
                    # This is text content, replace space with Thin Space
                    new_lines.append(line.replace(' ', '\u2009'))
            
            with open(srt_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            print("✨ Applied Thin Space Patch (U+2009) to SRT")
        except Exception as e:
            print(f"⚠️ Could not apply thin space patch: {e}")

        # Build style string
        # Default to DejaVu Sans if no font is provided and no file exists
        font_name = style.get('font_name', 'DejaVu Sans') 
        font_size = int(style.get('font_size', 24))
        color = style.get('color', 'white')
        outline = style.get('outline', True)
        
        # Position & Spacing
        margin_h = int(style.get('margin_horizontal', 40))
        margin_v = int(style.get('margin_vertical', style.get('margin_v', 70))) # Prefer vertical, fallback to v
        spacing = float(style.get('spacing', -1.0)) # Default to negative spacing for tight look
        shadow = int(style.get('shadow', 0))
        
        color_map = {
            'white': '&H00FFFFFF', 'black': '&H00000000',
            'yellow': '&H0000FFFF', 'red': '&H000000FF',
            'green': '&H0000FF00', 'blue': '&H00FF0000'
        }
        primary_color = color_map.get(str(color).lower(), '&H00FFFFFF')
        
        # Add MarginL, MarginR, MarginV, Alignment=2 (Centered), and Spacing
        style_string = f"FontName={font_name},FontSize={font_size},PrimaryColour={primary_color},MarginV={margin_v},MarginL={margin_h},MarginR={margin_h},Alignment=2,Spacing={spacing},Shadow={shadow}"
        
        if outline:
            # If explicit outline width is set in style, use it, otherwise default to 2
            outline_width = style.get('outline_width', 2)
            if isinstance(outline, bool) and outline:
                pass # keep default width
            elif isinstance(outline, (int, float)):
                outline_width = outline
                
            style_string += f",OutlineColour=&H00000000,BorderStyle=1,Outline={outline_width}"
        else:
            style_string += ",BorderStyle=1,Outline=0"
        
        # Burn subtitles with fontsdir support
        print(f"🔥 Burning captions using font: {font_name}")
        print(f"✨ Style: Spacing={spacing}, MarginV={margin_v}")
        print(f"📂 Local fonts dir: {FONTS_DIR}")
        
        # IMPORTANT: We add :fontsdir=... to the subtitles filter
        # This allows FFmpeg to find fonts located in your project/fonts folder
        vf_string = f"subtitles={srt_path}:fontsdir={FONTS_DIR}:force_style='{style_string}'"
        
        cmd = [
            'ffmpeg', '-i', input_path,
            '-vf', vf_string,
            '-c:a', 'copy',
            output_path, '-y'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Captions burned successfully!")
            response = send_file(
                output_path,
                mimetype='video/mp4',
                as_attachment=True,
                download_name='captioned_video.mp4'
            )
            @response.call_on_close
            def cleanup():
                time.sleep(2)
                for path in [input_path, srt_path, output_path]:
                    if os.path.exists(path): os.remove(path)
                print(f"🧹 Cleaned up job {job_id}")
            return response
        else:
            print(f"❌ FFmpeg error: {result.stderr}")
            return jsonify({'status': 'error', 'message': 'Failed to burn captions', 'error': result.stderr}), 500
    
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ Error: {e}")
        return jsonify({'status': 'error', 'message': str(e), 'trace': error_trace}), 500

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
            'remove_silence': '/remove-silence (POST)',
            'get_info': '/remove-silence/info (POST)',
            'burn_captions': '/burn-captions (POST)',  
            'create_srt': '/create-srt (POST)' 
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)