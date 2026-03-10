from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({
        'status': 'ok',
        'message': 'Hello from Vercel!',
        'version': '1.0'
    })

@app.route('/api/test')
def test():
    return jsonify({'success': True, 'data': 'test successful'})

# 這個是給 Vercel 用的
app = app
