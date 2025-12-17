import eventlet
eventlet.monkey_patch()
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, join_room, leave_room, send, emit
from flask import request
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
import jwt
from datetime import datetime, timedelta
from functools import wraps
from .agora_token_generator import generate_agora_token

app = Flask(__name__, static_url_path='/uploads', static_folder='uploads')

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = 'secret!'
# Настройка пути к базе данных
basedir = os.path.abspath(os.path.dirname(__file__))
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:///' + os.path.join(basedir, 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app)

online_users = {} # {user_id: sid}

# Модель пользователя
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)

# Модель для хранения контактов пользователей
class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # Уникальное ограничение, чтобы нельзя было добавить одного и того же контакта дважды
    __table_args__ = (db.UniqueConstraint('user_id', 'contact_id', name='_user_contact_uc'),)

# Модель чата
class Chat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# Модель сообщения
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    is_audio = db.Column(db.Boolean, default=False, nullable=False)

@app.route('/')
def index():
    return "Сервер мессенджера запущен!"

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'message': 'Отсутствует имя пользователя или пароль'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'message': 'Имя пользователя уже занято'}), 400

    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
    new_user = User(username=username, password=hashed_password)
    db.session.add(new_user)
    db.session.commit()

    return jsonify({'message': 'Пользователь успешно зарегистрирован'}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'message': 'Отсутствует имя пользователя или пароль'}), 400

    user = User.query.filter_by(username=username).first()

    if not user or not check_password_hash(user.password, password):
        return jsonify({'message': 'Неверное имя пользователя или пароль'}), 401

    # Создание токена
    token = jwt.encode({
        'user_id': user.id,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }, app.config['SECRET_KEY'], algorithm="HS256")

    return jsonify({'token': token}), 200

@app.route('/users/search', methods=['GET'])
def search_users():
    username_query = request.args.get('username', '')
    if not username_query:
        return jsonify({'message': 'Необходимо указать имя пользователя для поиска'}), 400

    # Поиск пользователей, чье имя содержит поисковый запрос
    users = User.query.filter(User.username.ilike(f'%{username_query}%')).all()

    # Формирование списка пользователей для ответа
    users_list = [{'id': user.id, 'username': user.username} for user in users]

    return jsonify(users_list), 200

# Декоратор для проверки токена
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'x-access-token' in request.headers:
            token = request.headers['x-access-token']

        if not token:
            return jsonify({'message': 'Токен отсутствует!'}), 401

        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = User.query.get(data['user_id'])
        except:
            return jsonify({'message': 'Токен недействителен!'}), 401

        return f(current_user, *args, **kwargs)

    return decorated

@app.route('/contacts/add', methods=['POST'])
@token_required
def add_contact(current_user):
    data = request.get_json()
    contact_id = data.get('contact_id')

    if not contact_id:
        return jsonify({'message': 'Необходимо указать contact_id'}), 400

    # Проверка, не пытается ли пользователь добавить сам себя
    if current_user.id == contact_id:
        return jsonify({'message': 'Нельзя добавить самого себя в контакты'}), 400

    # Проверка, существует ли такой пользователь
    contact_to_add = User.query.get(contact_id)
    if not contact_to_add:
        return jsonify({'message': 'Пользователь с таким id не найден'}), 404

    # Проверка, не добавлен ли уже этот контакт
    existing_contact = Contact.query.filter_by(user_id=current_user.id, contact_id=contact_id).first()
    if existing_contact:
        return jsonify({'message': 'Этот пользователь уже в ваших контактах'}), 400

    new_contact = Contact(user_id=current_user.id, contact_id=contact_id)
    db.session.add(new_contact)
    db.session.commit()

    return jsonify({'message': 'Контакт успешно добавлен'}), 201

@app.route('/contacts', methods=['GET'])
@token_required
def get_contacts(current_user):
    contacts = Contact.query.filter_by(user_id=current_user.id).all()
    contact_list = []
    for contact in contacts:
        contact_user = User.query.get(contact.contact_id)
        if contact_user:
            contact_list.append({'id': contact_user.id, 'username': contact_user.username})

    return jsonify(contact_list), 200

@app.route('/chats/create', methods=['POST'])
@token_required
def create_chat(current_user):
    data = request.get_json()
    contact_id = data.get('contact_id')

    if not contact_id:
        return jsonify({'message': 'Необходимо указать contact_id'}), 400

    # Проверка, есть ли такой контакт у пользователя
    contact = Contact.query.filter_by(user_id=current_user.id, contact_id=contact_id).first()
    if not contact:
        return jsonify({'message': 'Этого пользователя нет в ваших контактах'}), 403

    # Проверка, существует ли уже чат между этими пользователями
    # Чтобы избежать дублирования, проверяем обе комбинации user1_id и user2_id
    chat = Chat.query.filter(
        ((Chat.user1_id == current_user.id) & (Chat.user2_id == contact_id)) |
        ((Chat.user1_id == contact_id) & (Chat.user2_id == current_user.id))
    ).first()

    if chat:
        return jsonify({'message': 'Чат уже существует', 'chat_id': chat.id}), 200

    new_chat = Chat(user1_id=current_user.id, user2_id=contact_id)
    db.session.add(new_chat)
    db.session.commit()

    return jsonify({'message': 'Чат успешно создан', 'chat_id': new_chat.id}), 201

@app.route('/chats', methods=['GET'])
@token_required
def get_chats(current_user):
    # Находим все чаты, где пользователь является user1 или user2
    chats = Chat.query.filter((Chat.user1_id == current_user.id) | (Chat.user2_id == current_user.id)).all()
    chat_list = []
    for chat in chats:
        # Определяем ID другого участника чата
        other_user_id = chat.user2_id if chat.user1_id == current_user.id else chat.user1_id
        other_user = User.query.get(other_user_id)
        if other_user:
            chat_list.append({
                'chat_id': chat.id,
                'with_user': {
                    'id': other_user.id,
                    'username': other_user.username
                }
            })

    return jsonify(chat_list), 200

@app.route('/chats/<int:chat_id>/messages', methods=['GET'])
@token_required
def get_messages(current_user, chat_id):
    chat = Chat.query.get(chat_id)
    if not chat or (current_user.id not in [chat.user1_id, chat.user2_id]):
        return jsonify({'message': 'Чат не найден или у вас нет доступа'}), 404

    messages = Message.query.filter_by(chat_id=chat_id).order_by(Message.timestamp.asc()).all()
    message_list = []
    for msg in messages:
        sender = User.query.get(msg.sender_id)
        message_list.append({
            'id': msg.id,
            'sender': sender.username,
            'sender_id': msg.sender_id,
            'content': msg.content,
            'is_audio': msg.is_audio,
            'timestamp': msg.timestamp.isoformat()
        })
    return jsonify(message_list), 200

@app.route('/users/online', methods=['GET'])
@token_required
def get_online_users(current_user):
    return jsonify(list(online_users.keys())), 200

@app.route('/upload/audio', methods=['POST'])
@token_required
def upload_audio(current_user):
    if 'file' not in request.files:
        return jsonify({'message': 'Нет файла для загрузки'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'message': 'Файл не выбран'}), 400
    if file:
        filename = f"{current_user.id}_{int(datetime.now().timestamp())}.wav"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        return jsonify({'file_path': f'/uploads/{filename}'}), 201

@app.route('/messages/delete/<int:message_id>', methods=['DELETE'])
@token_required
def delete_message(current_user, message_id):
    message = Message.query.get(message_id)
    if not message:
        return jsonify({'message': 'Сообщение не найдено'}), 404
    
    if message.sender_id != current_user.id:
        return jsonify({'message': 'Нет прав для удаления этого сообщения'}), 403

    # Если сообщение было аудио, удаляем файл
    if message.is_audio:
        try:
            # Path is like /uploads/filename.wav, we need filename.wav
            filename = os.path.basename(message.content)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Error deleting audio file: {e}") # Log error, but don't block message deletion

    db.session.delete(message)
    db.session.commit()

    # Уведомляем клиентов в чате
    socketio.emit('message_deleted', {'message_id': message_id}, to=message.chat_id)

    return jsonify({'message': 'Сообщение удалено'}), 200

@app.route('/agora/token', methods=['POST'])
@token_required
def get_agora_token(current_user):
    data = request.get_json()
    channel_name = data.get('channelName')
    if not channel_name:
        return jsonify({'message': 'channelName is required'}), 400

    user_id = current_user.id
    token = generate_agora_token(channel_name, user_id)
    
    if token:
        return jsonify({'token': token})
    else:
        return jsonify({'message': 'Failed to generate Agora token'}), 500

# --- SocketIO Events ---

@socketio.on('connect')
def handle_connect():
    token = request.args.get('token')
    if not token:
        return False # Отклоняем соединение
    try:
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
        user_id = data['user_id']
        online_users[user_id] = request.sid
        print(f"User {user_id} connected with sid {request.sid}")
        # Уведомляем контакты, что пользователь в сети
        # (Это можно будет добавить позже для полной реализации)
    except Exception as e:
        print(f"Socket auth error: {e}")
        return False

@socketio.on('disconnect')
def handle_disconnect():
    user_id_to_remove = None
    for user_id, sid in online_users.items():
        if sid == request.sid:
            user_id_to_remove = user_id
            break
    if user_id_to_remove:
        del online_users[user_id_to_remove]
        print(f"User {user_id_to_remove} disconnected")
        # Уведомляем контакты, что пользователь вышел из сети
        # (Это можно будет добавить позже)


@socketio.on('join')
def on_join(data):
    # Здесь нужна проверка токена, но для простоты пока опустим
    # В реальном приложении токен нужно передавать при подключении
    username = data.get('username') # Предполагается, что клиент передает имя
    room = data.get('room') # room - это chat_id
    join_room(room)
    send(f'{username} присоединился к чату.', to=room)

@socketio.on('send_message')
def handle_send_message(data):
    token = data.get('token')
    room = data.get('room')
    content = data.get('content')
    is_audio = data.get('is_audio', False)

    if not token:
        # В реальном приложении здесь можно отправить ошибку клиенту
        return

    try:
        token_data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
        sender_id = token_data['user_id']
    except:
        return # Ошибка токена

    # Сохранение сообщения в БД
    new_message = Message(chat_id=room, sender_id=sender_id, content=content, is_audio=is_audio)
    db.session.add(new_message)
    db.session.commit()

    sender = User.query.get(sender_id)
    message_to_send = {
        'id': new_message.id,
        'sender': sender.username,
        'sender_id': sender_id,
        'content': content,
        'timestamp': new_message.timestamp.isoformat(),
        'is_audio': is_audio
    }
    send(message_to_send, to=room)

@socketio.on('call_user')
def handle_call_user(data):
    target_user_id = data.get('targetUserId')
    channel_name = data.get('channelName')
    token = data.get('token')

    caller_id = None
    for user_id, sid in online_users.items():
        if sid == request.sid:
            caller_id = user_id
            break
    
    if not caller_id:
        return

    caller = User.query.get(caller_id)
    if not caller:
        return

    target_sid = online_users.get(int(target_user_id))
    if target_sid:
        emit('incoming_call', {
            'callerId': caller_id,
            'callerUsername': caller.username,
            'channelName': channel_name,
            'token': token
        }, to=target_sid)

@socketio.on('answer_call')
def handle_answer_call(data):
    caller_id = data.get('callerId')
    caller_sid = online_users.get(int(caller_id))
    if caller_sid:
        emit('call_answered', {}, to=caller_sid)

@socketio.on('hang_up')
def handle_hang_up(data):
    other_user_id = data.get('otherUserId')
    other_user_sid = online_users.get(int(other_user_id))
    if other_user_sid:
        emit('call_ended', {}, to=other_user_sid)

# Создание базы данных и таблиц
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    socketio.run(app, debug=True)

