import tkinter as tk
from tkinter import scrolledtext, simpledialog, messagebox
import requests
import socketio
import jwt
import threading
import pyaudio
import wave
import pygame
import tempfile
import shutil
import agorartc as Rtc

# It's recommended to move App ID to an environment variable
AGORA_APP_ID = "96619c27fbeb4332b25e1413e8f3ce9f"

BASE_URL = 'https://messenger-with-app.onrender.com'

class MessengerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Alex")
        self.root.configure(bg='#2c2f33') # Dark background

        # Colors
        self.BG_COLOR = '#2c2f33'
        self.TEXT_COLOR = '#ffffff'
        self.PRIMARY_COLOR = '#7289da' # Discord-like blue/purple
        self.SECONDARY_COLOR = '#424549'
        self.token = None
        self.current_username = None
        self.sio = socketio.Client()
        self.message_widgets = {} # {message_id: widget}
        pygame.mixer.init() # Initialize pygame mixer for playback

        # Call state variables
        self.rtc_engine = None
        self.in_call = False
        self.current_call_info = {}
        self.incoming_call_window = None

        self.setup_login_window()
        self.setup_socketio_handlers()

    def setup_login_window(self):
        self.clear_window()
        self.root.geometry("300x150")

        tk.Label(self.root, text="Имя пользователя", bg=self.BG_COLOR, fg=self.TEXT_COLOR).pack(pady=5)
        self.username_entry = tk.Entry(self.root, bg=self.SECONDARY_COLOR, fg=self.TEXT_COLOR, insertbackground=self.TEXT_COLOR)
        self.username_entry.pack()

        tk.Label(self.root, text="Пароль", bg=self.BG_COLOR, fg=self.TEXT_COLOR).pack(pady=5)
        self.password_entry = tk.Entry(self.root, show="*", bg=self.SECONDARY_COLOR, fg=self.TEXT_COLOR, insertbackground=self.TEXT_COLOR)
        self.password_entry.pack()

        tk.Button(self.root, text="Вход", command=self.login, bg=self.PRIMARY_COLOR, fg=self.TEXT_COLOR, relief=tk.FLAT).pack(side=tk.LEFT, padx=10, pady=10)
        tk.Button(self.root, text="Регистрация", command=self.register, bg=self.PRIMARY_COLOR, fg=self.TEXT_COLOR, relief=tk.FLAT).pack(side=tk.RIGHT, padx=10, pady=10)

    def login(self):
        username = self.username_entry.get()
        password = self.password_entry.get()
        try:
            response = requests.post(f'{BASE_URL}/login', json={'username': username, 'password': password})
            if response.status_code == 200:
                self.token = response.json().get('token')
                self.current_username = username
                self.connect_socketio()
                self.setup_main_window()
            else:
                messagebox.showerror("Ошибка", response.json().get('message'))
        except requests.exceptions.ConnectionError:
            messagebox.showerror("Ошибка", "Не удалось подключиться к серверу.")

    def register(self):
        username = self.username_entry.get()
        password = self.password_entry.get()
        try:
            response = requests.post(f'{BASE_URL}/register', json={'username': username, 'password': password})
            messagebox.showinfo("Регистрация", response.json().get('message'))
        except requests.exceptions.ConnectionError:
            messagebox.showerror("Ошибка", "Не удалось подключиться к серверу.")

    def setup_main_window(self):
        self.clear_window()
        self.root.geometry("600x400")

        # Menu
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        actions_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Действия", menu=actions_menu)
        actions_menu.add_command(label="Поиск пользователя", command=self.search_user_dialog)
        actions_menu.add_command(label="Выйти", command=self.logout)

        # Main layout
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Chats list
        self.chats_listbox = tk.Listbox(main_frame, width=20, bg=self.SECONDARY_COLOR, fg=self.TEXT_COLOR, selectbackground=self.PRIMARY_COLOR, relief=tk.FLAT, borderwidth=0)
        self.chats_listbox.pack(side=tk.LEFT, fill=tk.Y)
        self.chats_listbox.bind('<<ListboxSelect>>', self.on_chat_select)

        # Chat window
        chat_frame = tk.Frame(main_frame, bg=self.BG_COLOR)
        chat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.chat_window = scrolledtext.ScrolledText(chat_frame, state='disabled', bg=self.SECONDARY_COLOR, fg=self.TEXT_COLOR, insertbackground=self.TEXT_COLOR)
        self.chat_window.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.chat_window.tag_config('sent', justify='right', background='#4f545c') # Darker sent bubble
        self.chat_window.tag_config('received', justify='left', background='#3a3d42') # Darker received bubble

        # Message entry
        message_frame = tk.Frame(chat_frame)
        message_frame.pack(fill=tk.X)
        self.message_entry = tk.Entry(message_frame)
        self.message_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.send_button = tk.Button(message_frame, text="Отправить", command=self.send_message)
        self.send_button.pack(side=tk.RIGHT)
        self.record_button = tk.Button(message_frame, text="Запись", command=self.toggle_recording)
        self.record_button.pack(side=tk.RIGHT, padx=5)

        self.call_button = tk.Button(message_frame, text="📞", command=self.start_call, bg=self.PRIMARY_COLOR, fg=self.TEXT_COLOR, relief=tk.FLAT)
        self.call_button.pack(side=tk.RIGHT)

        self.is_recording = False
        self.audio_frames = []

        self.load_chats()

    def load_chats(self):
        headers = {'x-access-token': self.token}
        # Загружаем онлайн пользователей
        online_response = requests.get(f'{BASE_URL}/users/online', headers=headers)
        online_user_ids = online_response.json() if online_response.status_code == 200 else []

        # Загружаем чаты
        chats_response = requests.get(f'{BASE_URL}/chats', headers=headers)
        if chats_response.status_code == 200:
            self.chats = chats_response.json()
            self.chats_listbox.delete(0, tk.END)
            for chat in self.chats:
                username = chat['with_user']['username']
                user_id = chat['with_user']['id']
                status = "● " if user_id in online_user_ids else ""
                self.chats_listbox.insert(tk.END, f"{status}{username}")
                if status:
                    self.chats_listbox.itemconfig(tk.END, {'fg': 'green'})
        else:
            messagebox.showerror("Ошибка", "Не удалось загрузить чаты.")

    def on_chat_select(self, event):
        selection = event.widget.curselection()
        if selection:
            index = selection[0]
            self.selected_chat = self.chats[index]
            self.load_messages()

    def load_messages(self):
        chat_id = self.selected_chat['chat_id']
        headers = {'x-access-token': self.token}
        response = requests.get(f'{BASE_URL}/chats/{chat_id}/messages', headers=headers)
        self.chat_window.config(state='normal')
        self.chat_window.delete(1.0, tk.END)
        self.message_widgets = {}
        if response.status_code == 200:
            messages = response.json()
            for msg in messages:
                tag = 'sent' if msg['sender'] == self.current_username else 'received'
                if msg.get('is_audio'):
                    self.add_audio_message_widget(msg, tag)
                else:
                    self.add_text_message_widget(msg, tag)
        self.chat_window.config(state='disabled')
        # Join socket.io room
        self.sio.emit('join', {'room': chat_id, 'username': self.current_username})

    def send_message(self):
        message = self.message_entry.get()
        if message and hasattr(self, 'selected_chat'):
            chat_id = self.selected_chat['chat_id']
            self.sio.emit('send_message', {'room': chat_id, 'content': message, 'token': self.token})
            self.message_entry.delete(0, tk.END)

    def search_user_dialog(self):
        query = simpledialog.askstring("Поиск", "Введите имя пользователя для поиска:")
        if query:
            headers = {'x-access-token': self.token}
            response = requests.get(f'{BASE_URL}/users/search?username={query}', headers=headers)
            users = response.json()
            # Simple display, can be improved with a custom dialog
            user_info = "\n".join([f"ID: {u['id']}, Имя: {u['username']}" for u in users])
            result = messagebox.askyesno("Результаты поиска", f"{user_info}\n\nДобавить первого пользователя в контакты?")
            if result and users:
                self.add_contact(users[0])

    def add_contact(self, user_data):
        contact_id = user_data['id']
        headers = {'x-access-token': self.token}
        add_response = requests.post(f'{BASE_URL}/contacts/add', json={'contact_id': contact_id}, headers=headers)
        
        message = add_response.json().get('message')
        messagebox.showinfo("Добавление контакта", message)

        # Если контакт успешно добавлен или уже существует, пытаемся создать чат
        if add_response.status_code == 201 or add_response.status_code == 400:
            chat_response = requests.post(f'{BASE_URL}/chats/create', json={'contact_id': contact_id}, headers=headers)
            if chat_response.status_code == 201 or chat_response.status_code == 200:
                messagebox.showinfo("Создание чата", f"Чат с {user_data['username']} готов.")
                self.load_chats() # Обновляем список чатов
            else:
                messagebox.showerror("Ошибка чата", chat_response.json().get('message'))

    def logout(self):
        self.sio.disconnect()
        self.token = None
        self.setup_login_window()


    def clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def connect_socketio(self):
        try:
            self.sio.connect(f'{BASE_URL}?token={self.token}')
        except socketio.exceptions.ConnectionError as e:
            messagebox.showerror("Ошибка WebSocket", f"Не удалось подключиться к серверу чата: {e}")

    def setup_socketio_handlers(self):
        @self.sio.on('message_deleted')
        def on_message_deleted(data):
            message_id = data.get('message_id')
            if message_id in self.message_widgets:
                self.chat_window.config(state='normal')
                # A bit tricky to remove a widget, let's just replace it with a placeholder
                # For a real app, a custom widget list would be better.
                self.chat_window.delete(self.message_widgets[message_id][0], self.message_widgets[message_id][1])
                self.chat_window.insert(self.message_widgets[message_id][0], "[Сообщение удалено]\n")
                self.chat_window.config(state='disabled')

        @self.sio.on('message')
        def on_message(data):
            if isinstance(data, dict) and hasattr(self, 'selected_chat'):
                self.chat_window.config(state='normal')
                tag = 'sent' if data['sender'] == self.current_username else 'received'
                if data.get('is_audio'):
                    self.add_audio_message_widget(data, tag)
                else:
                    self.chat_window.insert(tk.END, f"{data['sender']}: {data['content']}\n", tag)
                self.chat_window.config(state='disabled')
                self.chat_window.yview(tk.END)

        @self.sio.on('incoming_call')
        def on_incoming_call(data):
            self.root.after(0, self.show_incoming_call_popup, data)

        @self.sio.on('call_answered')
        def on_call_answered(data):
            self.root.after(0, self.handle_call_answered)

        @self.sio.on('call_ended')
        def on_call_ended(data):
            self.root.after(0, self.hang_up)


    def add_text_message_widget(self, msg, tag):
        self.chat_window.config(state='normal')
        start_index = self.chat_window.index(tk.END)
        
        container = tk.Frame(self.chat_window, bg=self.chat_window.tag_cget(tag, 'background'))
        
        label_text = f"{msg['sender']}: {msg['content']}"
        label = tk.Label(container, text=label_text, bg=self.chat_window.tag_cget(tag, 'background'), fg=self.TEXT_COLOR, wraplength=300, justify='left' if tag == 'received' else 'right')
        label.pack(side=tk.LEFT, pady=2, padx=5)

        if tag == 'sent':
            del_button = tk.Button(container, text="X", command=lambda mid=msg['id']: self.delete_message(mid), 
                                   bg='red', fg='white', relief=tk.FLAT, width=2)
            del_button.pack(side=tk.RIGHT)
        
        self.chat_window.window_create(tk.END, window=container)
        self.chat_window.insert(tk.END, '\n')
        end_index = self.chat_window.index(tk.END)
        self.message_widgets[msg['id']] = (start_index, end_index)
        self.chat_window.config(state='disabled')

    def add_audio_message_widget(self, msg, tag):
        self.chat_window.config(state='normal')
        start_index = self.chat_window.index(tk.END)

        container = tk.Frame(self.chat_window, bg=self.chat_window.tag_cget(tag, 'background'))
        label = tk.Label(container, text=f"{msg['sender']}:", bg=self.chat_window.tag_cget(tag, 'background'), fg=self.TEXT_COLOR)
        label.pack(side=tk.LEFT)
        play_button = tk.Button(container, text="▶ Воспроизвести", 
                                command=lambda url=msg['content']: self.play_audio(url), 
                                bg=self.PRIMARY_COLOR, fg=self.TEXT_COLOR, relief=tk.FLAT)
        play_button.pack(side=tk.LEFT, padx=5)

        if tag == 'sent':
            del_button = tk.Button(container, text="X", command=lambda mid=msg['id']: self.delete_message(mid), 
                                   bg='red', fg='white', relief=tk.FLAT, width=2)
            del_button.pack(side=tk.RIGHT)

        self.chat_window.window_create(tk.END, window=container)
        self.chat_window.insert(tk.END, '\n') # Newline after the widget
        end_index = self.chat_window.index(tk.END)
        self.message_widgets[msg['id']] = (start_index, end_index)
        self.chat_window.config(state='disabled')

    def delete_message(self, message_id):
        headers = {'x-access-token': self.token}
        response = requests.delete(f'{BASE_URL}/messages/delete/{message_id}', headers=headers)
        if response.status_code != 200:
            messagebox.showerror("Ошибка удаления", response.json().get('message'))

    def play_audio(self, url):
        try:
            full_url = f'{BASE_URL}{url}'
            response = requests.get(full_url, stream=True)
            if response.status_code == 200:
                # Save to a temporary file before playing
                with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp_file:
                    shutil.copyfileobj(response.raw, tmp_file)
                    tmp_filename = tmp_file.name
                
                pygame.mixer.music.load(tmp_filename)
                pygame.mixer.music.play()
            else:
                messagebox.showerror("Ошибка", "Не удалось загрузить аудиофайл.")
        except Exception as e:
            messagebox.showerror("Ошибка воспроизведения", str(e))

    def toggle_recording(self):
        if self.is_recording:
            self.is_recording = False
            self.record_button.config(text="Запись")
            self.stop_recording()
        else:
            self.is_recording = True
            self.record_button.config(text="Стоп")
            self.start_recording()

    def start_recording(self):
        self.audio_frames = []
        self.p_audio = pyaudio.PyAudio()
        self.stream = self.p_audio.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)
        threading.Thread(target=self._record_audio, daemon=True).start()

    def _record_audio(self):
        while self.is_recording:
            data = self.stream.read(1024)
            self.audio_frames.append(data)

    def stop_recording(self):
        self.stream.stop_stream()
        self.stream.close()
        self.p_audio.terminate()

        # Save to a temporary file
        temp_filename = "temp_audio.wav"
        with wave.open(temp_filename, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(self.p_audio.get_sample_size(pyaudio.paInt16))
            wf.setframerate(44100)
            wf.writeframes(b''.join(self.audio_frames))
        
        self.upload_and_send_audio(temp_filename)

    def upload_and_send_audio(self, filename):
        headers = {'x-access-token': self.token}
        with open(filename, 'rb') as f:
            files = {'file': (filename, f, 'audio/wav')}
            response = requests.post(f'{BASE_URL}/upload/audio', files=files, headers=headers)
        
        if response.status_code == 201:
            file_path = response.json().get('file_path')
            if file_path and hasattr(self, 'selected_chat'):
                chat_id = self.selected_chat['chat_id']
                self.sio.emit('send_message', {'room': chat_id, 'content': file_path, 'token': self.token, 'is_audio': True})
        else:
            messagebox.showerror("Ошибка загрузки", response.json().get('message'))

    # --- Agora Event Handler ---
    class AgoraEventHandler(Rtc.RtcEngineEventHandler):
        def __init__(self, app_instance):
            super().__init__()
            self.app = app_instance

        def onJoinChannelSuccess(self, connection, elapsed):
            print(f"Successfully joined channel {connection.channelId}")

        def onUserJoined(self, connection, remoteUid, elapsed):
            print(f"Remote user {remoteUid} joined")

        def onUserOffline(self, connection, remoteUid, reason):
            print(f"Remote user {remoteUid} left the channel")
            self.app.root.after(0, self.app.hang_up)

    # --- Call Methods ---
    def start_call(self):
        if not hasattr(self, 'selected_chat'):
            messagebox.showinfo("Звонок", "Выберите чат, чтобы позвонить.")
            return
        if self.in_call:
            self.hang_up()
            return

        target_user = self.selected_chat['with_user']
        channel_name = f"chat_{self.selected_chat['chat_id']}"

        headers = {'x-access-token': self.token}
        response = requests.post(f'{BASE_URL}/agora/token', json={'channelName': channel_name}, headers=headers)

        if response.status_code != 200:
            messagebox.showerror("Ошибка звонка", f"Не удалось получить токен: {response.json().get('message')}")
            return

        agora_token = response.json().get('token')

        self.sio.emit('call_user', {
            'targetUserId': target_user['id'],
            'channelName': channel_name,
            'token': agora_token
        })

        self.join_agora_channel(channel_name, agora_token)
        self.in_call = True
        self.call_button.config(text="❌")
        self.current_call_info = {'otherUserId': target_user['id']}

    def answer_call(self, data):
        if self.incoming_call_window:
            self.incoming_call_window.destroy()
            self.incoming_call_window = None

        channel_name = data['channelName']
        token = data['token']
        caller_id = data['callerId']

        self.join_agora_channel(channel_name, token)
        self.sio.emit('answer_call', {'callerId': caller_id})

        self.in_call = True
        self.call_button.config(text="❌")
        self.current_call_info = {'otherUserId': caller_id}

    def hang_up(self):
        if not self.in_call:
            return

        if self.current_call_info.get('otherUserId'):
            self.sio.emit('hang_up', {'otherUserId': self.current_call_info['otherUserId']})

        if self.rtc_engine:
            self.rtc_engine.leaveChannel()
            self.rtc_engine.release()
            self.rtc_engine = None

        if self.incoming_call_window:
            self.incoming_call_window.destroy()
            self.incoming_call_window = None

        self.call_button.config(text="📞")
        self.in_call = False
        self.current_call_info = {}

    def decline_call(self, data):
        if self.incoming_call_window:
            self.incoming_call_window.destroy()
            self.incoming_call_window = None
        # Here you could emit a 'call_declined' event if you want the caller to know.

    def handle_call_answered(self):
        messagebox.showinfo("Звонок", "Пользователь ответил на ваш звонок.")

    def join_agora_channel(self, channel_name, token):
        try:
            self.event_handler = self.AgoraEventHandler(self)
            self.rtc_engine = Rtc.RtcEngine(self.event_handler)
            self.rtc_engine.initialize(AGORA_APP_ID, Rtc.RtcEngineContext())
            self.rtc_engine.enableAudio()
            uid = int(jwt.decode(self.token, options={"verify_signature": False})['user_id'])
            self.rtc_engine.joinChannel(token, channel_name, "", uid)
        except Exception as e:
            messagebox.showerror("Agora Error", f"Ошибка инициализации Agora: {e}")
            if self.in_call:
                self.hang_up()

    def show_incoming_call_popup(self, data):
        if self.incoming_call_window or self.in_call:
            return

        self.incoming_call_window = tk.Toplevel(self.root)
        self.incoming_call_window.title("Входящий звонок")
        self.incoming_call_window.geometry("300x100")
        self.incoming_call_window.configure(bg=self.BG_COLOR)
        self.incoming_call_window.attributes("-topmost", True)

        caller_username = data.get('callerUsername', 'Unknown')
        label = tk.Label(self.incoming_call_window, text=f"Звонок от {caller_username}", bg=self.BG_COLOR, fg=self.TEXT_COLOR)
        label.pack(pady=10)

        button_frame = tk.Frame(self.incoming_call_window, bg=self.BG_COLOR)
        button_frame.pack(pady=5)

        answer_button = tk.Button(button_frame, text="Ответить", command=lambda: self.answer_call(data), bg='green', fg=self.TEXT_COLOR)
        answer_button.pack(side=tk.LEFT, padx=10)

        decline_button = tk.Button(button_frame, text="Отклонить", command=lambda: self.decline_call(data), bg='red', fg=self.TEXT_COLOR)
        decline_button.pack(side=tk.RIGHT, padx=10)

        self.incoming_call_window.protocol("WM_DELETE_WINDOW", lambda: self.decline_call(data))

if __name__ == '__main__':
    root = tk.Tk()
    app = MessengerApp(root)
    root.mainloop()
