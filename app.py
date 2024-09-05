import os
import sys
import json
import time
import requests
import numpy as np

from io import BytesIO
from sudoku import Sudoku
from replies import replies
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from random import randint, normalvariate, choice
from flask import Flask, send_file, make_response, url_for, request

from google.cloud import secretmanager

app = Flask(__name__)
FILE_PREF = '' if 'sudoku' in os.getcwd() else '/tmp/'


def create_sudoku(width, height, difficulty):
    puzzle = Sudoku(width, height).difficulty(difficulty)
    puzzle_data = puzzle.board, puzzle.height, puzzle.width
    solution = puzzle.solve()
    solution_data = solution.board, solution.height, solution.width
    return puzzle_data, solution_data


def draw_sudoku(board, height, width, cell_size=60,
                margin=20, line_width=3, cluster_line_width=6):
    rows = len(board)
    cols = len(board[0])
    img_width = cols * cell_size + 2 * margin
    img_height = rows * cell_size + 2 * margin
    img = Image.new('RGB', (img_width, img_height), color='white')
    draw = ImageDraw.Draw(img)
    for i in range(rows + 1):
        lw = cluster_line_width if i % height == 0 else line_width
        draw.line((margin, margin + i * cell_size,
                   img_width - margin, margin + i * cell_size),
                  fill='black', width=lw)
    for j in range(cols + 1):
        lw = cluster_line_width if j % width == 0 else line_width
        draw.line((margin + j * cell_size, margin,
                   margin + j * cell_size, img_height - margin),
                  fill='black', width=lw)
    try:
        font = ImageFont.truetype("Arial.ttf", int(cell_size * 0.7))
    except IOError:
        font = ImageFont.load_default()
    for i, row in enumerate(board):
        for j, num in enumerate(row):
            if num is not None:
                x = margin - (12 if num > 9 else 0) + j * cell_size + cell_size // 3
                y = margin -7 + i * cell_size + cell_size // 4
                draw.text((x, y), str(num), fill='black', font=font)
    return img


def load_secrets():
    client = secretmanager.SecretManagerServiceClient()
    project_id = "31722434708"
    secret_id = "insta-sudoku-daily"
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    secret_payload = response.payload.data.decode("UTF-8")
    secret_payload = secret_payload.replace("'", '"')
    return json.loads(secret_payload)


def update_secrets(old_secret, new_access_token):
    client = secretmanager.SecretManagerServiceClient()
    project_id = "31722434708"
    secret_id = "insta-sudoku-daily"
    secret_name = f"projects/{project_id}/secrets/{secret_id}"
    old_secret['access_token'] = new_access_token
    updated_secret_payload = json.dumps(old_secret)
    response = client.add_secret_version(
        request={"parent": secret_name,
                 "payload": {"data": updated_secret_payload.encode("UTF-8")}}
    )
    new_version_name = response.name
    versions = client.list_secret_versions(request={"parent": secret_name})
    for version in versions:
        if (version.name != new_version_name and
                version.state != secretmanager.SecretVersion.State.DESTROYED):
            client.destroy_secret_version(request={"name": version.name})


def refresh_token(app_id, app_secret, old_token):
    url = f"https://graph.facebook.com/v20.0/oauth/access_token?grant_type=fb_exchange_token&client_id={app_id}&client_secret={app_secret}&fb_exchange_token={old_token}"
    response = requests.get(url)
    new_token_data = response.json()
    new_token = new_token_data.get("access_token")
    if new_token:
        return new_token
    else:
        raise Exception(f"Error: {new_token_data}")


def verify_token_data(secret):
    debug_url = f"https://graph.facebook.com/v20.0/debug_token?input_token={secret['access_token']}&access_token={secret['access_token']}"
    response = requests.get(debug_url)
    token_data = response.json()
    if response.status_code == 200:
        expiration_datetime = datetime.fromtimestamp(
            token_data['data']['data_access_expires_at'])
        if (expiration_datetime - datetime.now()).days < 7:
            new_token = refresh_token(secret['app_id'],
                                      secret['app_secret'],
                                      secret['access_token'])
            update_secrets(secret, new_token)
    else:
        raise Exception(f"Error: {token_data}")


def normal_random_0_to_100(mean=50, stddev=15,
                           lower=0, upper=100):
    while True:
        value = normalvariate(mean, stddev)
        if lower <= value <= upper:
            return value


def get_hosted_image_url(image, imgbb_token):
    image_io = BytesIO()
    image.save(image_io, format='JPEG')
    image_io.seek(0)
    url = f'https://api.imgbb.com/1/upload?key={imgbb_token}&expiration=60'
    files = {'image': ('sudoku_puzzle.jpg', image_io, 'image/jpeg')}
    for i in range(3):
        try:
            response = requests.post(url, files=files, timeout=10, verify=False)
            response.raise_for_status()
            return response.json().get('data', {}).get('url')
        except Exception as e:
            print(f"Attempt {i+1}: Error uploading image: {e}", file=sys.stdout)
            time.sleep(2 ** i)
    return None


def host_image_freeimage(image, free_image_token):
    image_io = BytesIO()
    image.save(image_io, format='JPEG')
    image_io.seek(0)
    url = "https://freeimage.host/api/1/upload"
    files = {'source': image_io}
    data = {
        'key': free_image_token,
        'action': 'upload',
        'format': 'json'
    }
    response = requests.post(url, files=files, data=data)
    if response.status_code == 200:
        response_json = response.json()
        if response_json.get("status_code") == 200:
            return response_json['image']['display_url']
        else:
            raise Exception(f"Error in upload: {response_json.get('status_txt')}")
    else:
        response.raise_for_status()


def interact_with_latest_post(instagram_user_id, access_token):
    url = f"https://graph.facebook.com/v20.0/{instagram_user_id}/media?fields=id,caption&access_token={access_token}&limit=2"
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Error fetching latest post: {response.json()}")
    latest_post_data = response.json()
    if 'data' in latest_post_data and latest_post_data['data']:
        for post_data in latest_post_data['data']:
            latest_post_id = post_data['id']
            comments_url = f"https://graph.facebook.com/v20.0/{latest_post_id}/comments?access_token={access_token}"
            comments_response = requests.get(comments_url)
            comments_data = comments_response.json()
            if 'data' in comments_data and comments_data['data']:
                top_comments = comments_data['data'][:3]
                for comment in top_comments:
                    comment_url = f"https://graph.facebook.com/v20.0/{comment['id']}/replies"
                    comment_payload = {
                        'message': choice(replies),
                        'access_token': access_token
                    }
                    comment_response = requests.post(comment_url, data=comment_payload)
                    if comment_response.status_code == 200:
                        print(f"Replied to comment: {comment['id']}", file=sys.stdout)
                    else:
                        print(f"Failed to reply to comment: {comment['id']}, Error: {comment_response.json()}", file=sys.stdout)
    else:
        raise Exception("No posts found.")


@app.route('/', methods=['GET'])
def main_caller():
    return "Main Page", 200


@app.route('/upload_sudokus', methods=['GET'])
def upload_sudokus():
    width=randint(2, 5)
    height=randint(2, 4) if width == 5 else randint(
        3, 5) if width == 2 else randint(3, 4)
    difficulty=normal_random_0_to_100()/100
    puzzle_data, solution_data = create_sudoku(
        width, height, difficulty)
    empty = 0; full = 0
    for i in puzzle_data[0]:
        for j in i:
            if not j:
                empty += 1
            else:
                full += 1
    caption = f"""Enjoy today's ({datetime.now().strftime('%d %B %Y')})
Sudoku with cells {height}x{width} of difficulty {int(difficulty*100)}/100,
which has {full}/{empty + full} numbers.
.
.
.
#sudoku #sudokutime #sudokupuzzles #puzzle #puzzles #sudokuaddict #math
#kidsactivities #onlineclasses #brainteasers #funactivities #trainyourbrain
#maths #rubikscube #kidsactivity #education #puzzleaddict #activitiesforkids
#mathematics #challengeyourself #crossword #onlinepuzzles #numberpuzzles
#online #brainpuzzles #numberlandpuzzles #secondaryteacher #rjsclasses
#sudokoclass #onlinecoaching
"""
    print(caption, file=sys.stdout)

    puzzle_img = draw_sudoku(*puzzle_data)
    solution_img = draw_sudoku(*solution_data)
    solution_img = np.array(solution_img)
    noise = np.random.randint(0, 2, solution_img.shape, dtype='uint8')
    solution_img = np.clip(solution_img + noise, 0, 255)
    solution_img = Image.fromarray(solution_img)
    solution_img = solution_img.rotate(180)

    puzzle_img.save(f"{FILE_PREF}sudoku_puzzle.jpg", 'JPEG')
    solution_img.save(f"{FILE_PREF}sudoku_solution.jpg", 'JPEG')
    print('Generated Sudoku pair', file=sys.stdout)

    secret = load_secrets()
    try:
        interact_with_post(secret)
    except Exception as e:
        print(f'Exception handling interactions: {e}', file=sys.stdout)
    access_token = secret.get('access_token')
    instagram_user_id = secret.get('instagram_user_id')
    imgbb_token = secret.get('imgbb_token')
    free_image_token = secret.get('free_image_token')

    # image_urls = [get_hosted_image_url(puzzle_img, imgbb_token),
    #               get_hosted_image_url(solution_img, imgbb_token)]
    image_urls = [host_image_freeimage(puzzle_img, free_image_token),
                  host_image_freeimage(solution_img, free_image_token)]
    creation_ids = []
    for image_url in image_urls:
        upload_url = f"https://graph.facebook.com/v20.0/{instagram_user_id}/media"
        payload = {
            'image_url': image_url,
            'is_carousel_item': True,
            'access_token': access_token
        }
        upload_response = requests.post(upload_url, data=payload)
        upload_data = upload_response.json()
        creation_id = upload_data.get("id")
        if creation_id:
            creation_ids.append(creation_id)
            print(f"Uploaded image, Creation ID: {creation_id}", file=sys.stdout)
        else:
            print(f"Error uploading image: {upload_data}", file=sys.stdout)
    if len(creation_ids) == len(image_urls):
        carousel_payload = {
            'access_token': access_token,
            'caption': caption,
            'media_type': 'CAROUSEL',
            'children': ','.join(creation_ids)
        }
        carousel_url = f"https://graph.facebook.com/v20.0/{instagram_user_id}/media"
        carousel_response = requests.post(carousel_url, data=carousel_payload)
        carousel_data = carousel_response.json()
        if 'id' in carousel_data:
            carousel_creation_id = carousel_data['id']
            print(f"Carousel container created with ID: {carousel_creation_id}", file=sys.stdout)
        else:
            print(f"Error creating carousel container: {carousel_data}", file=sys.stdout)
        publish_url = f"https://graph.facebook.com/v20.0/{instagram_user_id}/media_publish"
        publish_payload = {
            'creation_id': carousel_creation_id,
            'access_token': access_token
        }
        publish_response = requests.post(publish_url, data=publish_payload)
        publish_data = publish_response.json()
        if 'id' in publish_data:
            print(f"Carousel post published with ID: {publish_data['id']}", file=sys.stdout)
        else:
            print(f"Error publishing carousel post: {publish_data}", file=sys.stdout)
    else:
        print("Not all images were uploaded successfully.", file=sys.stdout)
    verify_token_data(secret)
    return "Data Uploaded", 200


def interact_with_post(secret):
    instagram_user_id = secret.get('instagram_user_id')
    access_token = secret.get('access_token')
    try:
        interact_with_latest_post(instagram_user_id, access_token)
    except Exception as e:
        print(f'Error in interaction: {e}', file=sys.stdout)


@app.route('/sudoku_puzzle.jpg', methods=['GET'])
def serve_puzzle():
    image_path = f'{FILE_PREF}sudoku_puzzle.jpg'
    for _ in range(3):
        if os.path.exists(image_path):
            response = make_response(send_file(image_path, mimetype='image/jpeg'))
            response.headers['Content-Type'] = 'image/jpeg'
            response.headers['Access-Control-Allow-Methods'] = 'GET'
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return response
        else:
            time.sleep(1)
    return "Image not found", 404


@app.route('/sudoku_solution.jpg', methods=['GET'])
def serve_solution():
    image_path = f'{FILE_PREF}sudoku_solution.jpg'
    for _ in range(3):
        if os.path.exists(image_path):
            response = make_response(send_file(image_path, mimetype='image/jpeg'))
            response.headers['Content-Type'] = 'image/jpeg'
            response.headers['Access-Control-Allow-Methods'] = 'GET'
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return response
        else:
            time.sleep(1)
    return "Image not found", 404


@app.route('/logo.jpeg', methods=['GET'])
def serve_logo():
    image_path = 'logo.jpeg'
    if os.path.exists(image_path):
        return send_file(image_path, mimetype='image/jpeg')
    else:
        return "Image not found", 404


@app.route('/sitemap.xml', methods=['GET'])
def generate_sitemap():
    urls = []
    static_urls = [
        'serve_logo',
        'serve_puzzle',
        'serve_solution',
        'interact_with_post',
        'main_caller',
        'upload_sudokus',
    ]
    for rule in app.url_map.iter_rules():
        if rule.endpoint in static_urls:
            urls.append(url_for(rule.endpoint, _external=True))
    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>']
    sitemap.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url in urls:
        sitemap.append('<url>')
        sitemap.append(f'<loc>{url}</loc>')
        sitemap.append('</url>')
    sitemap.append('</urlset>')
    sitemap_xml = '\n'.join(sitemap)
    response = make_response(sitemap_xml)
    response.headers['Content-Type'] = 'application/xml'
    return response
