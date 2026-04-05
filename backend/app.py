import os
import gc
import json
import base64
import numpy as np
import cv2
from flask import Flask, request, jsonify
from flask_cors import CORS
import time

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuration
MAX_IMAGE_SIZE = 512  # Resize to max 512x512 for stability

def clamp(val):
    return np.clip(val, 0, 255)

def rgb_to_ycbcr(img_rgb):
    img = img_rgb.astype(np.float64)
    r, g, b = img[:,:,0], img[:,:,1], img[:,:,2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128
    return y, cb, cr

def ycbcr_to_rgb(y, cb, cr):
    r = y + 1.402 * (cr - 128)
    g = y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128)
    b = y + 1.772 * (cb - 128)
    img = np.stack([r, g, b], axis=-1)
    return np.clip(img, 0, 255).astype(np.uint8)

def dwt2_haar(data):
    rows, cols = data.shape
    if rows % 2 != 0: data = data[:-1, :]
    if cols % 2 != 0: data = data[:, :-1]
    
    a = data[0::2, 0::2]
    b = data[0::2, 1::2]
    c = data[1::2, 0::2]
    d = data[1::2, 1::2]
    
    ll = (a + b + c + d) / 2.0
    lh = (a - b + c - d) / 2.0
    hl = (a + b - c - d) / 2.0
    hh = (a - b - c + d) / 2.0
    
    return ll, lh, hl, hh

def idwt2_haar(ll, lh, hl, hh):
    h_rows, h_cols = ll.shape
    rows, cols = h_rows * 2, h_cols * 2
    data = np.zeros((rows, cols), dtype=np.float64)
    
    data[0::2, 0::2] = (ll + lh + hl + hh) / 2.0
    data[0::2, 1::2] = (ll - lh + hl - hh) / 2.0
    data[1::2, 0::2] = (ll + lh - hl - hh) / 2.0
    data[1::2, 1::2] = (ll - lh - hl + hh) / 2.0
    
    return data

def dwtN(data, level):
    current_ll = data
    subbands = []
    for _ in range(level):
        ll, lh, hl, hh = dwt2_haar(current_ll)
        subbands.append((lh, hl, hh))
        current_ll = ll
    return current_ll, subbands

def idwtN(ll, subbands):
    current_ll = ll
    for lh, hl, hh in reversed(subbands):
        current_ll = idwt2_haar(current_ll, lh, hl, hh)
    return current_ll

def process_image_input(file_storage, max_size=MAX_IMAGE_SIZE):
    try:
        file_bytes = file_storage.read()
        data = np.frombuffer(file_bytes, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Invalid image format")
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        h, w = img.shape[:2]
        if h > max_size or w > max_size:
            scale = max_size / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        
        return img
    except Exception as e:
        raise ValueError(f"Error processing image: {str(e)}")

def get_base64_img(img_rgb):
    try:
        if len(img_rgb.shape) == 2:
            img_rgb = cv2.merge([img_rgb, img_rgb, img_rgb])
            
        img_bgr = cv2.cvtColor(img_rgb.astype(np.uint8), cv2.COLOR_RGB2BGR)
        _, buffer = cv2.imencode('.png', img_bgr)
        return base64.b64encode(buffer).decode('utf-8')
    except Exception as e:
        print(f"B64 Error: {e}")
        return ""

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "watermark-backend", "time": time.time()})

def optimize_parameters(y_channel, watermark_bin, population_size=8, generations=5, mutation_rate=0.2):
    population = np.random.uniform(30, 150, population_size)
    best_q = 60
    best_fitness = -float('inf')
    
    for _ in range(generations):
        fitness_scores = []
        for q in population:
            est_psnr = 70 - 15 * np.log10(max(q, 1))
            est_robust = (q / 200) ** 0.5
            
            fitness = est_robust * 1000
            if est_psnr > 38:
                fitness += (est_psnr - 38) * 10
            else:
                fitness -= (38 - est_psnr) * 500
            
            fitness_scores.append(fitness)
            
        fitness_scores = np.array(fitness_scores)
        if np.max(fitness_scores) > best_fitness:
            best_fitness = np.max(fitness_scores)
            best_q = population[np.argmax(fitness_scores)]
            
        parents_idx = np.argsort(fitness_scores)[-2:]
        parents = population[parents_idx]
        
        new_pop = list(parents)
        while len(new_pop) < population_size:
            child = np.mean(parents) + np.random.normal(0, 20)
            child = np.clip(child, 20, 200)
            new_pop.append(child)
        population = np.array(new_pop)
        
    return float(best_q)

@app.route('/api/embed', methods=['POST'])
def embed():
    try:
        if 'cover' not in request.files or 'watermark' not in request.files:
            return jsonify({"error": "Missing files"}), 400
            
        dwt_level = int(request.form.get('dwtLevel', 1))
        block_size = int(request.form.get('blockSize', 4))
        
        cover = process_image_input(request.files['cover'])
        watermark = process_image_input(request.files['watermark'])
        
        factor = (2 ** dwt_level) * block_size
        h, w = cover.shape[:2]
        size = (min(h, w) // factor) * factor
        if size < factor:
            return jsonify({"error": "Image too small for selected parameters"}), 400
            
        cover = cv2.resize(cover, (size, size))
        y, cb, cr = rgb_to_ycbcr(cover)
        
        ll_size = size // (2 ** dwt_level)
        num_blocks = ll_size // block_size
        watermark = cv2.resize(watermark, (num_blocks, num_blocks))
        watermark_gray = cv2.cvtColor(watermark, cv2.COLOR_RGB2GRAY)
        watermark_bin = (watermark_gray > 127).astype(np.uint8)
        
        alpha = float(request.form.get('alpha')) if request.form.get('alpha') else optimize_parameters(y, watermark_bin)
            
        ll, subbands = dwtN(y, dwt_level)
        
        ll_mod = ll.copy()
        for i in range(num_blocks):
            for j in range(num_blocks):
                block = ll[i*block_size:(i+1)*block_size, j*block_size:(j+1)*block_size]
                try:
                    u, s, vh = np.linalg.svd(block)
                    s11 = s[0]
                    bit = watermark_bin[i, j]
                    
                    if bit == 1:
                        s11 = np.round((s11 - alpha / 2) / alpha) * alpha + alpha / 2
                    else:
                        s11 = np.round(s11 / alpha) * alpha
                    
                    s[0] = s11
                    block_mod = u @ np.diag(s) @ vh
                    ll_mod[i*block_size:(i+1)*block_size, j*block_size:(j+1)*block_size] = block_mod
                except np.linalg.LinAlgError:
                    continue
                
        y_mod = idwtN(ll_mod, subbands)
        result_rgb = ycbcr_to_rgb(y_mod, cb, cr)
        res_base64 = get_base64_img(result_rgb)
        
        del cover, watermark, y, cb, cr, ll, ll_mod, y_mod, result_rgb, subbands
        gc.collect()
        
        return jsonify({
            "image": f"data:image/png;base64,{res_base64}",
            "metrics": {"alpha": round(alpha, 2), "psnr": "N/A", "ssim": "N/A"}
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/extract', methods=['POST'])
def extract():
    try:
        if 'watermarked' not in request.files:
            return jsonify({"error": "Missing watermarked image"}), 400
            
        alpha = float(request.form.get('alpha', 60))
        dwt_level = int(request.form.get('dwtLevel', 1))
        block_size = int(request.form.get('blockSize', 4))
        
        watermarked = process_image_input(request.files['watermarked'])
        
        factor = (2 ** dwt_level) * block_size
        h, w = watermarked.shape[:2]
        size = (min(h, w) // factor) * factor
        watermarked = cv2.resize(watermarked, (size, size))
        
        y, cb, cr = rgb_to_ycbcr(watermarked)
        ll, subbands = dwtN(y, dwt_level)
        
        ll_size = size // (2 ** dwt_level)
        num_blocks = ll_size // block_size
        extracted_bin = np.zeros((num_blocks, num_blocks), dtype=np.uint8)
        
        for i in range(num_blocks):
            for j in range(num_blocks):
                block = ll[i*block_size:(i+1)*block_size, j*block_size:(j+1)*block_size]
                try:
                    _, s, _ = np.linalg.svd(block)
                    s11 = s[0]
                    d0 = abs(s11 - np.round(s11 / alpha) * alpha)
                    d1 = abs(s11 - (np.round((s11 - alpha / 2) / alpha) * alpha + alpha / 2))
                    extracted_bin[i, j] = 255 if d1 < d0 else 0
                except:
                    continue
                
        res_base64 = get_base64_img(extracted_bin)
        
        del watermarked, y, cb, cr, ll, subbands, extracted_bin
        gc.collect()
        
        return jsonify({"image": f"data:image/png;base64,{res_base64}"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze():
    try:
        if 'image' not in request.files:
            return jsonify({"error": "Missing image"}), 400
            
        img = process_image_input(request.files['image'])
        y, cb, cr = rgb_to_ycbcr(img)
        ll, lh, hl, hh = dwt2_haar(y)
        
        previews = {
            "LL": f"data:image/png;base64,{get_base64_img(ll)}",
            "LH": f"data:image/png;base64,{get_base64_img(lh)}",
            "HL": f"data:image/png;base64,{get_base64_img(hl)}",
            "HH": f"data:image/png;base64,{get_base64_img(hh)}"
        }
        
        del img, y, cb, cr, ll, lh, hl, hh
        gc.collect()
        
        return jsonify({"previews": previews})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/stress-test', methods=['POST'])
def stress_test():
    try:
        if 'image' not in request.files or 'watermark' not in request.files:
            return jsonify({"error": "Missing files"}), 400
            
        alpha = float(request.form.get('alpha', 60))
        dwt_level = int(request.form.get('dwtLevel', 1))
        block_size = int(request.form.get('blockSize', 4))
        attacks = json.loads(request.form.get('attacks', '[]'))
        
        img_orig = process_image_input(request.files['image'])
        wm_orig = process_image_input(request.files['watermark'])
        
        results = []
        for attack in attacks:
            attack_id = attack.get('id')
            intensity = attack.get('intensity', 0.5)
            
            img_attacked = img_orig.copy()
            if attack_id == 'compression':
                quality = int(100 - intensity * 70)
                _, buffer = cv2.imencode('.jpg', cv2.cvtColor(img_attacked, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
                img_attacked = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
                img_attacked = cv2.cvtColor(img_attacked, cv2.COLOR_BGR2RGB)
            elif attack_id == 'noise':
                noise = np.random.normal(0, intensity * 50, img_attacked.shape).astype(np.int16)
                img_attacked = np.clip(img_attacked.astype(np.int16) + noise, 0, 255).astype(np.uint8)
            elif attack_id == 'blur':
                k = int(intensity * 10) * 2 + 1
                img_attacked = cv2.GaussianBlur(img_attacked, (k, k), 0)
            
            h, w = img_attacked.shape[:2]
            factor = (2 ** dwt_level) * block_size
            size = (min(h, w) // factor) * factor
            img_attacked = cv2.resize(img_attacked, (size, size))
            y, _, _ = rgb_to_ycbcr(img_attacked)
            ll, _ = dwtN(y, dwt_level)
            
            ll_size = size // (2 ** dwt_level)
            num_blocks = ll_size // block_size
            
            wm_ref = cv2.resize(wm_orig, (num_blocks, num_blocks))
            wm_ref_gray = cv2.cvtColor(wm_ref, cv2.COLOR_RGB2GRAY)
            wm_ref_bin = (wm_ref_gray > 127).astype(np.uint8)
            
            extracted_bin = np.zeros((num_blocks, num_blocks), dtype=np.uint8)
            for i in range(num_blocks):
                for j in range(num_blocks):
                    block = ll[i*block_size:(i+1)*block_size, j*block_size:(j+1)*block_size]
                    try:
                        _, s, _ = np.linalg.svd(block)
                        s11 = s[0]
                        d0 = abs(s11 - np.round(s11 / alpha) * alpha)
                        d1 = abs(s11 - (np.round((s11 - alpha / 2) / alpha) * alpha + alpha / 2))
                        extracted_bin[i, j] = 1 if d1 < d0 else 0
                    except:
                        continue
            
            nc = np.sum(wm_ref_bin * extracted_bin) / np.sqrt(np.sum(wm_ref_bin**2) * np.sum(extracted_bin**2)) if np.sum(wm_ref_bin**2) > 0 and np.sum(extracted_bin**2) > 0 else 0
            results.append({"name": attack_id, "score": float(nc)})
            
            del img_attacked, wm_ref, wm_ref_gray, wm_ref_bin, y, ll, extracted_bin
            
        del img_orig, wm_orig
        gc.collect()
        
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": "Internal Server Error", "details": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port)

