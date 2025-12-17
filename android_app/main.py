from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.properties import StringProperty, ListProperty, ObjectProperty
from kivy.network.urlrequest import UrlRequest
from kivy.uix.popup import Popup
from kivy.uix.label import Label
from kivy.clock import mainthread
from kivy.utils import platform
import socketio
import threading
import json
import jwt
import pyaudio
import wave
import pygame
import requests
import tempfile
import shutil

if platform == 'android':
    from android.permissions import request_permissions, Permission
    request_permissions([Permission.RECORD_AUDIO])

BASE_URL = 'http://127.0.0.1:5000'

class LoginScreen(Screen):
    pass

class ChatScreen(Screen):
    chats_list = ListProperty([])
    def on_enter(self, *args):
        App.get_running_app().load_chats()

class MessengerApp(App):
    token = StringProperty(None)
    selected_chat = ObjectProperty(None)
    is_recording = False
    audio_frames = []

    def build(self):
        pygame.mixer.init()
        self.sio = socketio.Client()
        self.setup_socketio_handlers()
        self.sm = ScreenManager()
        self.sm.add_widget(LoginScreen(name='login'))
        self.sm.add_widget(ChatScreen(name='chat'))
        return self.sm

    def _api_request(self, endpoint, method='GET', body=None, headers=None, on_success=None, on_failure=None):
        url = f'{BASE_URL}{endpoint}'
        req_headers = {'Content-Type': 'application/json'}
        if self.token:
            req_headers['x-access-token'] = self.token
        if headers:
            req_headers.update(headers)
        req_body = json.dumps(body).encode('utf-8') if body else None
        def _on_success(req, result):
            if on_success: on_success(result)
        def _on_failure(req, result):
            msg = result.get('message', 'Ошибка') if isinstance(result, dict) else str(result)
            self.show_popup('Ошибка', msg)
            if on_failure: on_failure(result)
        UrlRequest(url, method=method, req_body=req_body, req_headers=req_headers, on_success=_on_success, on_failure=_on_failure, on_error=_on_failure)

    def login(self, username, password):
        def on_login_success(result):
            self.token = result.get('token')
            self.connect_socketio()
            self.sm.current = 'chat'
        self._api_request('/login', method='POST', body={'username': username, 'password': password}, on_success=on_login_success)

    def register(self, username, password):
        def on_register_success(result):
            self.show_popup('Регистрация', result.get('message', 'Успешно'))
        self._api_request('/register', method='POST', body={'username': username, 'password': password}, on_success=on_register_success)

    def load_chats(self):
        def on_online_users_loaded(online_ids):
            def on_chats_loaded(chats):
                chat_screen = self.sm.get_screen('chat')
                chat_screen.chats_list = chats
                data = []
                for i, chat in enumerate(chats):
                    user = chat['with_user']
                    status = " (в сети)" if user['id'] in online_ids else ""
                    data.append({'text': f"{user['username']}{status}", 'on_press': lambda i=i: self.select_chat(i)})
                chat_screen.ids.chats_rv.data = data
            self._api_request('/chats', on_success=on_chats_loaded)
        self._api_request('/users/online', on_success=on_online_users_loaded)

    def select_chat(self, index):
        chat_screen = self.sm.get_screen('chat')
        self.selected_chat = chat_screen.chats_list[index]
        self.load_messages()

    def load_messages(self):
        def on_load_success(result):
            self.update_messages_display(result)
            self.sio.emit('join', {'room': self.selected_chat['chat_id']})
        self._api_request(f"/chats/{self.selected_chat['chat_id']}/messages", on_success=on_load_success)

    def play_audio(self, url):
        def _play():
            try:
                full_url = f'{BASE_URL}{url}'
                response = requests.get(full_url, stream=True)
                if response.status_code == 200:
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp_file:
                        shutil.copyfileobj(response.raw, tmp_file)
                        tmp_filename = tmp_file.name
                    pygame.mixer.music.load(tmp_filename)
                    pygame.mixer.music.play()
                else:
                    self.show_popup("Ошибка", "Не удалось загрузить аудиофайл.")
            except Exception as e:
                self.show_popup("Ошибка воспроизведения", str(e))
        threading.Thread(target=_play).start()

    @mainthread
    def update_messages_display(self, messages):
        chat_screen = self.sm.get_screen('chat')
        try:
            decoded_token = jwt.decode(self.token, options={"verify_signature": False})
            current_user_id = decoded_token['user_id']
        except:
            current_user_id = -1
        
        data = []
        for msg in messages:
            halign = 'right' if msg['sender_id'] == current_user_id else 'left'
            data.append({
                'sender_text': msg['sender'],
                'message_text': msg['content'], 
                'is_audio': msg.get('is_audio', False),
                'audio_url': msg['content'] if msg.get('is_audio') else '',
                'halign': halign,
                'message_id': msg.get('id')
            })
        chat_screen.ids.messages_rv.data = data

    def delete_message(self, message_id):
        if message_id == -1: return
        self._api_request(f'/messages/delete/{message_id}', method='DELETE')

    def setup_socketio_handlers(self):
        @self.sio.on('message_deleted')
        def on_message_deleted(data):
            message_id = data.get('message_id')
            chat_screen = self.sm.get_screen('chat')
            for item in chat_screen.ids.messages_rv.data:
                if item.get('message_id') == message_id:
                    chat_screen.ids.messages_rv.data.remove(item)
                    break

        @self.sio.on('message')
        def on_message(data):
            if isinstance(data, dict) and self.selected_chat:
                try:
                    decoded_token = jwt.decode(self.token, options={"verify_signature": False})
                    current_user_id = decoded_token['user_id']
                except:
                    current_user_id = -1
                halign = 'right' if data.get('sender_id') == current_user_id else 'left'
                self.sm.get_screen('chat').ids.messages_rv.data.append({
                    'sender_text': data.get('sender'),
                    'message_text': data.get('content'), 
                    'is_audio': data.get('is_audio', False),
                    'audio_url': data.get('content') if data.get('is_audio') else '',
                    'halign': halign,
                    'message_id': data.get('id')
                })

    def send_message(self):
        chat_screen = self.sm.get_screen('chat')
        message_text = chat_screen.ids.message_input.text
        if message_text and self.selected_chat:
            self.sio.emit('send_message', {'room': self.selected_chat['chat_id'], 'content': message_text, 'token': self.token})
            chat_screen.ids.message_input.text = ''

    def toggle_recording(self):
        if self.is_recording:
            self.is_recording = False
            self.sm.get_screen('chat').ids.record_button.text = "Запись"
            self.stop_recording()
        else:
            self.is_recording = True
            self.sm.get_screen('chat').ids.record_button.text = "Стоп"
            self.start_recording()

    def start_recording(self):
        self.audio_frames = []
        self.p_audio = pyaudio.PyAudio()
        self.stream = self.p_audio.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)
        threading.Thread(target=self._record_audio, daemon=True).start()

    def _record_audio(self):
        while self.is_recording:
            try:
                data = self.stream.read(1024)
                self.audio_frames.append(data)
            except IOError: # Stream closed
                break

    def stop_recording(self):
        if self.stream.is_active():
            self.stream.stop_stream()
        self.stream.close()
        self.p_audio.terminate()
        temp_filename = "temp_audio.wav"
        with wave.open(temp_filename, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(self.p_audio.get_sample_size(pyaudio.paInt16))
            wf.setframerate(44100)
            wf.writeframes(b''.join(self.audio_frames))
        self.upload_and_send_audio(temp_filename)

    def upload_and_send_audio(self, filename):
        def _upload():
            headers = {'x-access-token': self.token}
            with open(filename, 'rb') as f:
                files = {'file': (filename, f, 'audio/wav')}
                try:
                    response = requests.post(f'{BASE_URL}/upload/audio', files=files, headers=headers)
                    if response.status_code == 201:
                        file_path = response.json().get('file_path')
                        if file_path and self.selected_chat:
                            chat_id = self.selected_chat['chat_id']
                            self.sio.emit('send_message', {'room': chat_id, 'content': file_path, 'token': self.token, 'is_audio': True})
                    else:
                        self.show_popup("Ошибка загрузки", response.json().get('message'))
                except requests.exceptions.RequestException as e:
                    self.show_popup("Ошибка сети", str(e))
        threading.Thread(target=_upload).start()

    def connect_socketio(self):
        try:
            self.sio.connect(f'{BASE_URL}?token={self.token}')
        except Exception as e:
            print(f"SocketIO connection error: {e}")

    def show_popup(self, title, message):
        popup = Popup(title=title, content=Label(text=str(message)), size_hint=(0.8, 0.4))
        popup.open()

if __name__ == '__main__':
    MessengerApp().run()