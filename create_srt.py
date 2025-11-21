#!/usr/bin/env python3
import json

def format_timestamp(seconds):
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def create_srt_from_words(words, output_file, words_per_line=5, all_caps=False):
    """Create SRT file from ElevenLabs word timestamps"""
    
    # Validate inputs
    if not isinstance(words, list):
        raise TypeError(f"words must be a list, got {type(words)}")
    
    if not words:
        raise ValueError("words list is empty")
    
    # Convert words_per_line to int if it's a string
    if isinstance(words_per_line, str):
        words_per_line = int(words_per_line)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        subtitle_index = 1
        
        for i in range(0, len(words), words_per_line):
            chunk = words[i:i + words_per_line]
            
            if not chunk:
                continue
            
            # Validate word structure
            if not isinstance(chunk[0], dict):
                raise TypeError(f"Each word must be a dict, got {type(chunk[0])}")
            
            if 'start' not in chunk[0] or 'end' not in chunk[-1] or 'text' not in chunk[0]:
                raise ValueError(f"Word missing required fields. Got: {chunk[0]}")
            
            start_time = float(chunk[0]['start'])
            end_time = float(chunk[-1]['end'])
            text = ' '.join([str(word.get('text', '')) for word in chunk])
            
            # Apply uppercase if requested
            if all_caps:
                text = text.upper()
            
            f.write(f"{subtitle_index}\n")
            f.write(f"{format_timestamp(start_time)} --> {format_timestamp(end_time)}\n")
            f.write(f"{text}\n\n")
            
            subtitle_index += 1
    
    print(f"✅ Created SRT file: {output_file} ({subtitle_index - 1} subtitles)")
    return subtitle_index - 1

def create_word_by_word_srt(words, output_file, all_caps=False):
    """Create SRT with one word per subtitle (TikTok style)"""
    
    # Validate input
    if not isinstance(words, list):
        raise TypeError(f"words must be a list, got {type(words)}")
    
    if not words:
        raise ValueError("words list is empty")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for i, word in enumerate(words, 1):
            # Validate word structure
            if not isinstance(word, dict):
                raise TypeError(f"Each word must be a dict, got {type(word)}")
            
            if 'start' not in word or 'end' not in word or 'text' not in word:
                raise ValueError(f"Word missing required fields. Got: {word}")
            
            start_time = float(word['start'])
            end_time = float(word['end'])
            text = str(word['text'])
            
            # Apply uppercase if requested
            if all_caps:
                text = text.upper()
            
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(start_time)} --> {format_timestamp(end_time)}\n")
            f.write(f"{text}\n\n")
    
    print(f"✅ Created word-by-word SRT: {output_file} ({len(words)} words)")
    return len(words)