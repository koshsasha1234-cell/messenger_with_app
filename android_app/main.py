from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.properties import StringProperty, ListProperty, BooleanProperty, NumericProperty, ObjectProperty
from kivy.uix.popup import Popup
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.boxlayout import BoxLayout
from kivy.network.urlrequest import UrlRequest
from kivy.clock import mainthread
from kivy.utils import platform
from urllib.parse import quote
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
from agora_rtc_sdk import RtcEngine, RtcEngineEventHandler, RtcEngineContext

if platform == 'android':
    from android.permissions import request_permissions, Permission
    request_permissions([Permission.RECORD_AUDIO])

BASE_URL = 'https://messenger-with-app.onrender.com'
AGORA_APP_ID = "96619c27fbeb4332b25e1413e8f3ce9f"

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

    # Call state
    rtc_engine = None
    in_call = False
    current_call_info = {}
    incoming_call_popup = None

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
            if on_success:
                on_success(result)

        def _on_failure(req, result):
            msg = result.get('message', 'Неизвестная ошибка') if isinstance(result, dict) else str(result)
            self.show_popup('Ошибка', msg)
            if on_failure:
                on_failure(result)

        def _on_error(req, error):
            self.show_popup('Сетевая ошибка', str(error))
            if on_failure:
                on_failure(error)

        UrlRequest(url, method=method, req_body=req_body, req_headers=req_headers,
                   on_success=_on_success, on_failure=_on_failure, on_error=_on_error)

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

        @self.sio.on('incoming_call')
        @mainthread
        def on_incoming_call(data):
            self.show_incoming_call_popup(data)

        @self.sio.on('call_answered')
        @mainthread
        def on_call_answered(data):
            self.show_popup("Звонок", "Пользователь ответил на ваш звонок.")

        @self.sio.on('call_ended')
        @mainthread
        def on_call_ended(data):
            self.hang_up()

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
                        self._show_popup_threadsafe("Ошибка загрузки", response.json().get('message'))
                except requests.exceptions.RequestException as e:
                    self._show_popup_threadsafe("Ошибка сети", str(e))
        threading.Thread(target=_upload).start()

    @mainthread
    def _show_popup_threadsafe(self, title, text):
        self.show_popup(title, text)

    def connect_socketio(self):
        try:
            self.sio.connect(f'{BASE_URL}?token={self.token}')
        except Exception as e:
            print(f"SocketIO connection error: {e}")

    def on_stop(self):
        if self.sio.connected:
            self.sio.disconnect()

    def search_friend(self):
        chat_screen = self.sm.get_screen('chat')
        search_query = chat_screen.ids.search_input.text
        if not search_query:
            return

        self._api_request(
            f'/users/search?username={quote(search_query)}',
            method='GET',
            on_success=self.show_search_results
        )

    def show_search_results(self, results):
        if not results:
            self.show_popup("Поиск", "Пользователи не найдены.")
            return

        content = BoxLayout(orientation='vertical', spacing='5dp')
        popup_title = "Результаты поиска"
        
        for user in results:
            btn = Button(text=f"Добавить {user['username']}")
            btn.bind(on_press=lambda x, u=user: self.add_friend(u))
            content.add_widget(btn)

        popup = Popup(title=popup_title, content=content, size_hint=(0.8, 0.6))
        self.search_popup = popup
        popup.open()

    def add_friend(self, user_data):
        if hasattr(self, 'search_popup'):
            self.search_popup.dismiss()

        contact_id = user_data['id']
        
        def on_chat_create_success(result):
            self.show_popup("Успех", f"Чат с {user_data['username']} создан.")
            self.load_chats()

        def on_add_success(result):
            self._api_request(
                '/chats/create',
                method='POST',
                body={'contact_id': contact_id},
                on_success=on_chat_create_success
            )

        self._api_request(
            '/contacts/add',
            method='POST',
            body={'contact_id': contact_id},
            on_success=on_add_success
        )

    def show_popup(self, title, text):
        content = Button(text='OK')
        popup = Popup(title=title,
                      content=content,
                      size_hint=(None, None), size=(400, 200))
        content.bind(on_press=popup.dismiss)
        popup.open()

    # --- Call Methods ---

    class AgoraEventHandler(RtcEngineEventHandler):
        def __init__(self, app_instance):
            super().__init__()
            self.app = app_instance

        def onJoinChannelSuccess(self, connection, elapsed):
            print(f"Successfully joined channel {connection.channelId}")

        def onUserJoined(self, connection, remoteUid, elapsed):
            print(f"Remote user {remoteUid} joined")

        def onUserOffline(self, connection, remoteUid, reason):
            print(f"Remote user {remoteUid} left the channel")
            self.app.hang_up()

    def start_call(self):
        if self.in_call:
            self.hang_up()
            return

        if not self.selected_chat:
            self.show_popup("Звонок", "Выберите чат для звонка.")
            return

        target_user = self.selected_chat['with_user']
        channel_name = f"chat_{self.selected_chat['chat_id']}"

        def on_token_success(result):
            agora_token = result.get('token')
            if not agora_token:
                self.show_popup("Ошибка звонка", "Не удалось получить токен.")
                return

            self.sio.emit('call_user', {
                'targetUserId': target_user['id'],
                'channelName': channel_name,
                'token': agora_token
            })

            self.join_agora_channel(channel_name, agora_token)
            self.in_call = True
            self.sm.get_screen('chat').ids.call_button.text = "Завершить"
            self.current_call_info = {'otherUserId': target_user['id']}

        self._api_request('/agora/token', method='POST', body={'channelName': channel_name}, on_success=on_token_success)

    def answer_call(self, data):
        if self.incoming_call_popup:
            self.incoming_call_popup.dismiss()
            self.incoming_call_popup = None
        
        channel_name = data['channelName']
        token = data['token']
        caller_id = data['callerId']

        self.join_agora_channel(channel_name, token)
        self.sio.emit('answer_call', {'callerId': caller_id})
        self.in_call = True
        self.sm.get_screen('chat').ids.call_button.text = "Завершить"
        self.current_call_info = {'otherUserId': caller_id}

    @mainthread
    def hang_up(self):
        if not self.in_call:
            return

        if self.current_call_info.get('otherUserId'):
            self.sio.emit('hang_up', {'otherUserId': self.current_call_info['otherUserId']})

        if self.rtc_engine:
            self.rtc_engine.leaveChannel()
            self.rtc_engine.release()
            self.rtc_engine = None

        if self.incoming_call_popup:
            self.incoming_call_popup.dismiss()
            self.incoming_call_popup = None

        self.sm.get_screen('chat').ids.call_button.text = "Звонок"
        self.in_call = False
        self.current_call_info = {}

    def decline_call(self, data):
        if self.incoming_call_popup:
            self.incoming_call_popup.dismiss()
            self.incoming_call_popup = None

    def join_agora_channel(self, channel_name, token):
        try:
            if platform == 'android':
                request_permissions([Permission.RECORD_AUDIO])
            
            self.event_handler = self.AgoraEventHandler(self)
            self.rtc_engine = RtcEngine.create(self.event_handler)
            context = RtcEngineContext()
            context.appId = AGORA_APP_ID
            self.rtc_engine.initialize(context)
            self.rtc_engine.enableAudio()
            uid = int(jwt.decode(self.token, options={"verify_signature": False})['user_id'])
            self.rtc_engine.joinChannel(token, channel_name, "", uid)
        except Exception as e:
            self.show_popup("Agora Error", f"Ошибка инициализации Agora: {e}")
            if self.in_call:
                self.hang_up()

    def show_incoming_call_popup(self, data):
        if self.incoming_call_popup or self.in_call:
            return

        caller_username = data.get('callerUsername', 'Unknown')
        
        content = BoxLayout(orientation='vertical', spacing='10dp', padding='10dp')
        label = Label(text=f'Входящий звонок от {caller_username}')
        buttons = BoxLayout(spacing='10dp')
        
        answer_btn = Button(text='Ответить', background_color=(0,1,0,1))
        decline_btn = Button(text='Отклонить', background_color=(1,0,0,1))

        buttons.add_widget(answer_btn)
        buttons.add_widget(decline_btn)
        content.add_widget(label)
        content.add_widget(buttons)

        popup = Popup(title='Входящий звонок', content=content, size_hint=(0.8, 0.4), auto_dismiss=False)
        
        answer_btn.bind(on_press=lambda x: self.answer_call(data))
        decline_btn.bind(on_press=lambda x: self.decline_call(data))
        decline_btn.bind(on_press=popup.dismiss)

        self.incoming_call_popup = popup
        self.incoming_call_popup.open()

if __name__ == '__main__':
    MessengerApp().run()