import os
from flask import Flask, request, jsonify, make_response
from flasgger import Swagger
from google.cloud import storage
from dotenv import load_dotenv
import mysql.connector
from dicttoxml import dicttoxml

# --- Cargar variables del archivo .env ---
load_dotenv()

app = Flask(__name__)

# --- Configuración de Swagger ---
app.config['SWAGGER'] = {
    'title': 'Gestor de Imágenes - Microservicio Flask',
    'uiversion': 3
}
swagger = Swagger(app)

# --- Configuración de conexión a MariaDB ---
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB")
    )

# --- Configuración del cliente de Google Cloud Storage ---
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
storage_client = storage.Client()
bucket = storage_client.bucket(os.getenv("GCP_BUCKET_NAME"))

# --- Función para devolver respuesta JSON o XML ---
def format_response(data, code=200):
    if request.headers.get('Accept') == 'application/xml':
        xml_data = dicttoxml(data, custom_root='response', attr_type=False)
        response = make_response(xml_data, code)
        response.headers['Content-Type'] = 'application/xml'
        return response
    else:
        return make_response(jsonify(data), code)

# --- Subir imagen ---
@app.route('/images', methods=['POST'])
def upload_image():
    """
    Subir una imagen al bucket y registrar en la base de datos
    ---
    consumes:
      - multipart/form-data
    parameters:
      - name: file
        in: formData
        type: file
        required: true
        description: Imagen a subir
      - name: Accept
        in: header
        type: string
        enum: [application/json, application/xml]
        required: false
        description: Formato de respuesta deseado
    responses:
      200:
        description: Imagen subida correctamente
        examples:
          application/json: { "message": "Imagen subida correctamente", "filename": "foto.png" }
    """
    if 'file' not in request.files:
        return format_response({'error': 'No se encontró el archivo'}, 400)

    file = request.files['file']
    blob = bucket.blob(file.filename)
    blob.upload_from_file(file, content_type=file.content_type)
    blob.make_public()

    access_url = blob.public_url

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO images (filename, filesize_bytes, mime_type, access_url)
        VALUES (%s, %s, %s, %s)
    """, (file.filename, file.content_length, file.content_type, access_url))
    conn.commit()
    cursor.close()
    conn.close()

    return format_response({
        'message': 'Imagen subida correctamente',
        'filename': file.filename,
        'access_url': access_url
    })

# --- Listar imágenes ---
@app.route('/images', methods=['GET'])
def list_images():
    """
    Listar todas las imágenes registradas
    ---
    produces:
      - application/json
      - application/xml
    parameters:
      - name: Accept
        in: header
        type: string
        enum: [application/json, application/xml]
        required: false
        description: Formato de respuesta deseado
    responses:
      200:
        description: Lista de imágenes en JSON o XML
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM images")
    images = cursor.fetchall()
    cursor.close()
    conn.close()
    return format_response({'images': images})

# --- Actualizar imagen ---
@app.route('/images/<int:image_id>', methods=['PUT'])
def update_image(image_id):
    """
    Actualizar una imagen existente (reemplaza el archivo y actualiza los datos)
    ---
    consumes:
      - multipart/form-data
    parameters:
      - name: image_id
        in: path
        type: integer
        required: true
        description: ID de la imagen a actualizar
      - name: file
        in: formData
        type: file
        required: true
        description: Nuevo archivo de imagen
      - name: Accept
        in: header
        type: string
        enum: [application/json, application/xml]
        required: false
    responses:
      200:
        description: Imagen actualizada correctamente
    """
    if 'file' not in request.files:
        return format_response({'error': 'No se encontró el archivo'}, 400)

    file = request.files['file']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT filename FROM images WHERE id = %s", (image_id,))
    old = cursor.fetchone()

    if not old:
        return format_response({'error': 'Imagen no encontrada'}, 404)

    # Eliminar imagen anterior del bucket
    old_blob = bucket.blob(old['filename'])
    old_blob.delete()

    # Subir nueva imagen
    blob = bucket.blob(file.filename)
    blob.upload_from_file(file, content_type=file.content_type)
    blob.make_public()

    cursor.execute("""
        UPDATE images
        SET filename = %s, filesize_bytes = %s, mime_type = %s, access_url = %s
        WHERE id = %s
    """, (file.filename, file.content_length, file.content_type, blob.public_url, image_id))
    conn.commit()
    cursor.close()
    conn.close()

    return format_response({'message': 'Imagen actualizada correctamente', 'new_url': blob.public_url})

# --- Eliminar imagen ---
@app.route('/images/<int:image_id>', methods=['DELETE'])
def delete_image(image_id):
    """
    Eliminar una imagen del bucket y de la base de datos
    ---
    parameters:
      - name: image_id
        in: path
        type: integer
        required: true
        description: ID de la imagen a eliminar
      - name: Accept
        in: header
        type: string
        enum: [application/json, application/xml]
        required: false
    responses:
      200:
        description: Imagen eliminada correctamente
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT filename FROM images WHERE id = %s", (image_id,))
    img = cursor.fetchone()

    if not img:
        return format_response({'error': 'Imagen no encontrada'}, 404)

    # Eliminar del bucket
    blob = bucket.blob(img['filename'])
    blob.delete()

    cursor.execute("DELETE FROM images WHERE id = %s", (image_id,))
    conn.commit()
    cursor.close()
    conn.close()

    return format_response({'message': 'Imagen eliminada correctamente'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

