from flask import Flask, render_template, Response, request, jsonify
import cv2
import numpy as np
import google.generativeai as genai
import threading
import pyaudio
import wave
from gtts import gTTS
import os
import time
from datetime import datetime
import speech_recognition as sr
from PIL import Image
import pygame
import queue
from googletrans import Translator
from flask_cors import CORS
import base64
import traceback
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

class VideoTeachingAssistant:
    def __init__(self):
        api_key = os.getenv('GOOGLE_API_KEY')
        if not api_key:
            raise ValueError("Google API key not found in environment variables")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")
        self.cap = cv2.VideoCapture(0)
        self.is_recording_audio = False
        self.audio_frames = []
        self.audio_queue = queue.Queue()
        self.audio_format = pyaudio.paInt16
        self.channels = 1
        self.rate = 44100
        self.chunk = 1024
        self.audio = pyaudio.PyAudio()
        pygame.mixer.init()
        os.makedirs("media/captured_images", exist_ok=True)
        os.makedirs("media/recorded_audio", exist_ok=True)
        os.makedirs("media/generated_audio", exist_ok=True)
        self.latest_response = ""
        self.latest_transcribed_text = ""
        self.translator = Translator()

    def start_video_stream(self):
        while True:
            ret, frame = self.cap.read()
            if not ret:
                break
            ret, jpeg = cv2.imencode('.jpg', frame)
            frame = jpeg.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n\r\n')

    def capture_image(self, frame):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_path = f"media/captured_images/capture_{timestamp}.jpg"
        cv2.imwrite(image_path, frame)
        pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        response = self.model.generate_content(["analyze the image and if it is the question then solve the question (make sure that while giving answer dont use bold/italic)", pil_image])
        self.latest_response = response.text
        print("Image Analysis:", response.text)
        return self.latest_response

    def start_audio_recording(self):
        self.is_recording_audio = True
        self.audio_frames = []
        def audio_recording_thread():
            stream = self.audio.open(format=self.audio_format, channels=self.channels, rate=self.rate, input=True, frames_per_buffer=self.chunk)
            while self.is_recording_audio:
                data = stream.read(self.chunk)
                self.audio_frames.append(data)
            stream.stop_stream()
            stream.close()
        threading.Thread(target=audio_recording_thread).start()

    def stop_audio_recording(self):
        self.is_recording_audio = False
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_path = f"media/recorded_audio/recording_{timestamp}.wav"
        wf = wave.open(audio_path, 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(self.audio.get_sample_size(self.audio_format))
        wf.setframerate(self.rate)
        wf.writeframes(b''.join(self.audio_frames))
        wf.close()
        recognizer = sr.Recognizer()
        with sr.AudioFile(audio_path) as source:
            audio = recognizer.record(source)
            try:
                text = recognizer.recognize_google(audio)
                print("Transcribed Text:", text)
                self.latest_transcribed_text = text
                response = self.model.generate_content(text)
                self.latest_response = response.text
                print("Assistant Response:", response.text)
                return self.latest_response, self.latest_transcribed_text
            except sr.UnknownValueError:
                print("Could not understand audio")
                return "Could not understand audio", "Could not understand audio"
            except sr.RequestError as e:
                print("Error with the speech recognition service:", str(e))
                return f"Error with the speech recognition service: {str(e)}", "Error occurred during transcription"

    def text_to_speech(self, text, lang="en"):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            audio_path = f"media/generated_audio/response_{timestamp}.mp3"
            tts = gTTS(text=text, lang=lang)
            tts.save(audio_path)
            return audio_path
        except Exception as e:
            print(f"Error in text_to_speech: {str(e)}")
            raise

    async def translate_text(self, text, target_language):
        try:
            translated = self.translator.translate(text, dest=target_language)
            audio_path = self.text_to_speech(translated.text, target_language)
            return translated.text, audio_path
        except Exception as e:
            print(f"Error in translate_text: {str(e)}")
            raise

    def cleanup(self):
        self.cap.release()
        cv2.destroyAllWindows()
        self.audio.terminate()

assistant = VideoTeachingAssistant()

@app.route('/')
def index():
    return render_template('index.html', response=assistant.latest_response)

@app.route('/video_feed')
def video_feed():
    return Response(assistant.start_video_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/capture_image', methods=['POST'])
def capture_image():
    try:
        frame = assistant.cap.read()[1]
        response_text = assistant.capture_image(frame)
        return jsonify({"response": response_text})
    except Exception as e:
        print("Error processing image:", str(e))
        return jsonify({"error": "Failed to process image"}), 500

@app.route('/start_audio_recording', methods=['POST'])
def start_audio_recording():
    assistant.start_audio_recording()
    return "Audio recording started."

@app.route('/stop_audio_recording', methods=['POST'])
def stop_audio_recording():
    response_text, transcribed_text = assistant.stop_audio_recording()
    return jsonify({
        "response": response_text,
        "transcribed_text": transcribed_text
    })

@app.route('/translate', methods=['POST'])
async def translate():
    try:
        data = request.get_json()
        text = data['text']
        target_language = data['language']
        translated_text, audio_path = await assistant.translate_text(text, target_language)
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Generated audio file not found: {audio_path}")
        audio_url = f"/{audio_path}"
        return jsonify({"translated_text": translated_text, "audio_url": audio_url})
    except Exception as e:
        print(f"Error in translation: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(port=3001)