import requests
import json

BASE_URL = 'http://127.0.0.1:5000'

def register(username, password):
    response = requests.post(f'{BASE_URL}/register', json={'username': username, 'password': password})
    print(response.json())

def login(username, password):
    response = requests.post(f'{BASE_URL}/login', json={'username': username, 'password': password})
    if response.status_code == 200:
        print("Вход выполнен успешно!")
        return response.json().get('token')
    else:
        print(response.json())
        return None

def search_users(token, username_query):
    headers = {'x-access-token': token}
    response = requests.get(f'{BASE_URL}/users/search?username={username_query}', headers=headers)
    print(response.json())

def add_contact(token, contact_id):
    headers = {'x-access-token': token}
    response = requests.post(f'{BASE_URL}/contacts/add', json={'contact_id': contact_id}, headers=headers)
    print(response.json())

def get_contacts(token):
    headers = {'x-access-token': token}
    response = requests.get(f'{BASE_URL}/contacts', headers=headers)
    print(response.json())


def main():
    token = None
    while True:
        if not token:
            print("\n1. Регистрация")
            print("2. Вход")
            print("3. Выход")
            choice = input("Выберите действие: ")

            if choice == '1':
                username = input("Введите имя пользователя: ")
                password = input("Введите пароль: ")
                register(username, password)
            elif choice == '2':
                username = input("Введите имя пользователя: ")
                password = input("Введите пароль: ")
                token = login(username, password)
            elif choice == '3':
                break
            else:
                print("Неверный выбор.")
        else:
            print("\n1. Поиск пользователей")
            print("2. Добавить контакт")
            print("3. Показать контакты")
            print("4. Выйти из аккаунта")
            choice = input("Выберите действие: ")

            if choice == '1':
                query = input("Введите имя для поиска: ")
                search_users(token, query)
            elif choice == '2':
                contact_id = input("Введите ID пользователя для добавления: ")
                add_contact(token, int(contact_id))
            elif choice == '3':
                get_contacts(token)
            elif choice == '4':
                token = None
            else:
                print("Неверный выбор.")

if __name__ == '__main__':
    main()
