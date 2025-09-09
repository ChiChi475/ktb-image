import os
import requests
import json
import zipfile
import re
from datetime import datetime
import pytz
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# --- Cấu hình ---
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = "generated-zips"
CONFIG_FILE = os.path.join(REPO_ROOT, "generator", "config.json")

# Giới hạn dung lượng repo GitHub
MAX_REPO_SIZE_MB = 900

# --- Các hàm hỗ trợ ---

def get_trimmed_image_with_padding(image, max_padding_x=40, max_padding_y=20):
    """Trims transparent borders but keeps a specified maximum padding."""
    bbox = image.getbbox()
    if not bbox:
        return None
    x1, y1, x2, y2 = bbox
    width, height = image.size
    new_x1 = max(0, x1 - max_padding_x)
    new_y1 = max(0, y1 - max_padding_y)
    new_x2 = min(width, x2 + max_padding_x)
    new_y2 = min(height, y2 + max_padding_y)
    return image.crop((new_x1, new_y1, new_x2, new_y2))

def load_config():
    """Tải cấu hình từ file config.json."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Lỗi: Không tìm thấy tệp {CONFIG_FILE}!")
        return {}
    except json.JSONDecodeError:
        print(f"Lỗi: File {CONFIG_FILE} không phải là file JSON hợp lệ.")
        return {}

def download_image(url):
    """Tải ảnh từ URL và trả về đối tượng PIL Image."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': url
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return Image.open(BytesIO(response.content)).convert("RGBA")
    except Exception as e:
        print(f"Lỗi khi tải ảnh từ {url}: {e}")
        return None

def clean_title(title, keywords):
    """Làm sạch tiêu đề bằng cách loại bỏ các từ khóa không phân biệt chữ hoa/thường."""
    cleaned_keywords = []
    for k in keywords:
        keyword_parts = re.split(r'[- ]', k.strip())
        escaped_parts = [re.escape(part) for part in keyword_parts]
        flexible_k = r'(?:-|\s)?'.join(escaped_parts)
        cleaned_keywords.append(flexible_k)

    cleaned_keywords.sort(key=len, reverse=True)
    pattern = r'\b(' + '|'.join(cleaned_keywords) + r')\b'
    cleaned_title = re.sub(pattern, '', title, flags=re.IGNORECASE).strip()
    return cleaned_title.replace('-', ' ').replace('  ', ' ')

def process_image(design_img, mockup_img, mockup_config, user_config):
    """Cắt, trim và dán design vào mockup."""
    # 1. Xóa nền bằng thuật toán "magic wand"
    design_w, design_h = design_img.size
    seed_color = design_img.getpixel((0, 0))
    seed_r, seed_g, seed_b = seed_color[:3]
    pixels = design_img.load()
    stack = [(0, 0)]
    visited = set()
    
    while stack:
        x, y = stack.pop()
        if (x, y) in visited or not (0 <= x < design_w and 0 <= y < design_h):
            continue
        visited.add((x, y))
        
        current_pixel = pixels[x, y]
        current_r, current_g, current_b = current_pixel[:3]
        
        if abs(current_r - seed_r) < 30 and abs(current_g - seed_g) < 30 and abs(current_b - seed_b) < 30:
            pixels[x, y] = (0, 0, 0, 0)
            stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])

    # 2. Cắt lại ảnh sau khi xóa nền
    trimmed_design = get_trimmed_image_with_padding(design_img)
    if not trimmed_design:
        return None

    # 3. Dán design vào mockup
    mockup_w, mockup_h = mockup_config['w'], mockup_config['h']
    design_w, design_h = trimmed_design.size
    scale = min(mockup_w / design_w, mockup_h / design_h)
    final_w, final_h = int(design_w * scale), int(design_h * scale)
    resized_design = trimmed_design.resize((final_w, final_h), Image.Resampling.LANCZOS)
    
    final_x = mockup_config['x'] + (mockup_w - final_w) // 2
    final_y = mockup_config['y'] + 20
    
    final_mockup = mockup_img.copy()
    final_mockup.paste(resized_design, (final_x, final_y), resized_design)
    
    # 4. Thêm chữ ký
    watermark_content = user_config.get("watermark_text")
    if watermark_content:
        if watermark_content.startswith(('http://', 'https://')):
            # Thêm chữ ký dạng ảnh
            watermark_img = download_image(watermark_content)
            if watermark_img:
                max_wm_width = 280
                wm_w, wm_h = watermark_img.size
                if wm_w > max_wm_width:
                    aspect_ratio = wm_h / wm_w
                    new_w = max_wm_width
                    new_h = int(new_w * aspect_ratio)
                    watermark_img = watermark_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                wm_w, wm_h = watermark_img.size
                paste_x = final_mockup.width - wm_w - 20
                paste_y = final_mockup.height - wm_h - 50
                final_mockup.paste(watermark_img, (paste_x, paste_y), watermark_img)
        else:
            # Thêm chữ ký dạng text
            draw = ImageDraw.Draw(final_mockup)
            try:
                font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verdanab.ttf")
                font = ImageFont.truetype(font_path, 100)
            except IOError:
                font = ImageFont.load_default()
            text_bbox = draw.textbbox((0, 0), watermark_content, font=font)
            text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
            text_x = final_mockup.width - text_w - 20
            text_y = final_mockup.height - text_h - 50
            draw.text((text_x, text_y), watermark_content, fill=(0, 0, 0, 128), font=font)
            
    return final_mockup

def get_repo_size(path='.'):
    """Tính toán kích thước của repo hiện tại."""
    total_size = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)
    return total_size / (1024 * 1024)

def cleanup_old_zips():
    """Xóa TOÀN BỘ file .zip trong thư mục output khi action bắt đầu."""
    output_path = os.path.join(REPO_ROOT, OUTPUT_DIR)
    if not os.path.exists(output_path):
        return
    print("Bắt đầu dọn dẹp tất cả các file zip cũ...")
    for filename in os.listdir(output_path):
        if filename.endswith(".zip"):
            file_path = os.path.join(output_path, filename)
            try:
                os.remove(file_path)
                print(f"Đã xóa: {filename}")
            except Exception as e:
                print(f"Lỗi khi xóa file {filename}: {e}")
    print("Dọn dẹp hoàn tất.")

# --- CÁC HÀM LOGIC MỚI ---
def load_processed_log(filepath):
    """Tải lịch sử các URL đã xử lý từ file JSON."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("Không tìm thấy file log đã xử lý, coi như chạy lần đầu.")
        return {} # Trả về dict rỗng nếu file không tồn tại
    except json.JSONDecodeError:
        print("Lỗi đọc file log, file có thể bị hỏng. Bắt đầu với log rỗng.")
        return {}

def save_processed_log(filepath, data):
    """Lưu lịch sử các URL đã xử lý vào file JSON."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        print(f"Đã lưu lịch sử xử lý vào {filepath}")
    except Exception as e:
        print(f"Lỗi khi lưu file log: {e}")

# --- Logic chính ---
def main():
    print("Bắt đầu quy trình tự động generate mockup.")
    
    # 1. Thiết lập ban đầu
    output_path = os.path.join(REPO_ROOT, OUTPUT_DIR)
    if not os.path.exists(output_path):
        os.makedirs(output_path)
    
    cleanup_old_zips()

    # 2. Tải cấu hình và các thiết lập mới
    configs = load_config()
    if not configs:
        return # Dừng nếu không tải được config
        
    settings = configs.get("settings", {})
    max_urls_per_domain = settings.get("max_urls_per_domain", 200)
    history_to_keep = settings.get("history_to_keep", 10)
    log_file_name = settings.get("processed_log_file", "processed_urls.json")
    log_file_path = os.path.join(REPO_ROOT, log_file_name)
    
    defaults = configs.get("defaults", {})
    domains_configs = configs.get("domains", {})
    mockup_sets_config = configs.get("mockup_sets", {})
    title_clean_keywords = defaults.get("title_clean_keywords", [])

    # 3. Tải lịch sử đã xử lý
    processed_log = load_processed_log(log_file_path)
    
    images_for_zip = {}
    urls_summary = {}
    
    # 4. Lặp qua tất cả các domain trong config
    for domain, domain_rules in domains_configs.items():
        print(f"\n--- Bắt đầu xử lý domain: {domain} ---")

        # Kiểm tra dung lượng repo
        if get_repo_size(REPO_ROOT) >= MAX_REPO_SIZE_MB:
            print(f"Cảnh báo: Dung lượng repo đã vượt quá {MAX_REPO_SIZE_MB}MB. Dừng lại.")
            break
        
        # Sắp xếp rule để ưu tiên pattern dài hơn
        domain_rules.sort(key=lambda x: len(x['pattern']), reverse=True)

        # 5. Xác định URL cần xử lý
        try:
            urls_url = f"https://raw.githubusercontent.com/ktbihow/image-crawler/main/{domain}.txt"
            all_urls_content = requests.get(urls_url).text
            all_urls = [line.strip() for line in all_urls_content.splitlines() if line.strip()]
        except Exception as e:
            print(f"Lỗi: Không thể tải file URL cho domain {domain}. Bỏ qua. {e}")
            continue

        # Lấy lịch sử và chuyển thành Set để tìm kiếm hiệu quả
        last_processed_urls = processed_log.get(domain, [])
        stop_urls_set = set(last_processed_urls)
        
        urls_to_process = []
        for i, url in enumerate(all_urls):
            if url in stop_urls_set:
                print(f"Đã tìm thấy URL dừng trong lịch sử: {url}")
                break
            if i >= max_urls_per_domain:
                print(f"Đã đạt giới hạn {max_urls_per_domain} URL cho domain này.")
                break
            urls_to_process.append(url)
            
        if not urls_to_process:
            print(f"Không có URL mới nào cần xử lý cho domain {domain}.")
            continue
            
        print(f"Tìm thấy {len(urls_to_process)} URL mới để xử lý cho {domain}.")
        
        mockup_cache = {}
        processed_count = 0
        skipped_count = 0
        successfully_processed_this_run = []

        # 6. Xử lý từng URL
        for url in urls_to_process:
            filename = os.path.basename(url)
            matched_rule = next((rule for rule in domain_rules if rule["pattern"] in filename), None)

            if not matched_rule or matched_rule.get("action") == "skip":
                skipped_count += 1
                continue

            try:
                img = download_image(url)
                if not img:
                    skipped_count += 1
                    continue

                crop_coords = matched_rule.get("coords")
                if not crop_coords:
                    print(f"Không có tọa độ crop trong rule cho file {filename}. Bỏ qua.")
                    skipped_count += 1
                    continue

                pixel = img.getpixel((crop_coords['x'], crop_coords['y']))
                avg_brightness = sum(pixel[:3]) / 3
                is_white = avg_brightness > 128

                if (matched_rule.get("skipWhite") and is_white) or \
                   (matched_rule.get("skipBlack") and not is_white):
                    skipped_count += 1
                    continue

                cropped_img = img.crop((crop_coords['x'], crop_coords['y'], crop_coords['x'] + crop_coords['w'], crop_coords['y'] + crop_coords['h']))

                for mockup_name in matched_rule.get("mockup_sets_to_use", []):
                    # Cache on-demand
                    if mockup_name not in mockup_cache:
                        if mockup_name in mockup_sets_config:
                            m_config = mockup_sets_config[mockup_name]
                            mockup_cache[mockup_name] = {
                                "white": download_image(m_config.get("white")),
                                "black": download_image(m_config.get("black")),
                                "coords": m_config.get("coords"),
                                "watermark_text": m_config.get("watermark_text"),
                                "title_prefix_to_add": m_config.get("title_prefix_to_add", ""),
                                "title_suffix_to_add": m_config.get("title_suffix_to_add", "")
                            }
                        else: continue

                    mockup_data = mockup_cache.get(mockup_name)
                    if not mockup_data: continue
                    
                    mockup_to_use = mockup_data["white"] if is_white else mockup_data["black"]

                    if not mockup_to_use: continue
                    
                    user_config = {"watermark_text": mockup_data.get("watermark_text")}
                    final_mockup = process_image(cropped_img.copy(), mockup_to_use, mockup_data.get("coords"), user_config)

                    if not final_mockup: continue

                    base_filename = os.path.splitext(filename)[0]
                    cleaned_title = clean_title(base_filename.replace('-', ' ').strip(), title_clean_keywords)
                    prefix = mockup_data.get("title_prefix_to_add", "")
                    suffix = mockup_data.get("title_suffix_to_add", "")
                    final_filename = f"{prefix} {cleaned_title} {suffix}".replace('  ', ' ').strip() + '.jpg'
                    
                    img_byte_arr = BytesIO()
                    final_mockup_rgb = final_mockup.convert('RGB')
                    final_mockup.save(img_byte_arr, format="JPEG", quality=90)
                    
                    if mockup_name not in images_for_zip:
                        images_for_zip[mockup_name] = []
                    images_for_zip[mockup_name].append((final_filename, img_byte_arr.getvalue()))
                
                successfully_processed_this_run.append(url)
                processed_count += 1

            except Exception as e:
                print(f"Lỗi khi xử lý ảnh {url}: {e}")
                skipped_count += 1
        
        urls_summary[domain] = {'processed': processed_count, 'skipped': skipped_count, 'total_to_process': len(urls_to_process)}

        # 7. Cập nhật log cho domain hiện tại
        if successfully_processed_this_run:
            previous_urls_for_domain = processed_log.get(domain, [])
            updated_urls = successfully_processed_this_run + previous_urls_for_domain
            processed_log[domain] = updated_urls[:history_to_keep]

    # 8. Tạo file ZIP
    for mockup_name, image_list in images_for_zip.items():
        if not image_list: continue
        total_images = len(image_list)
        zip_filename = f"{mockup_name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}_{total_images}_images.zip"
        zip_path = os.path.join(output_path, zip_filename)
        print(f"Đang tạo file: {zip_path} với {total_images} ảnh.")
        with zipfile.ZipFile(zip_path, 'w') as zf:
            for filename, data in image_list:
                zf.writestr(filename, data)

    # 9. Lưu lại toàn bộ lịch sử đã xử lý và ghi log tóm tắt
    save_processed_log(log_file_path, processed_log)
    write_log(urls_summary)
    print("Kết thúc quy trình.")

def write_log(urls_summary):
    """Ghi tóm tắt kết quả generate vào file generate_log.txt."""
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    now_vietnam = datetime.now(vietnam_tz)
    log_file_path = os.path.join(REPO_ROOT, "generate_log.txt")
    with open(log_file_path, "w", encoding="utf-8") as f:
        f.write(f"--- Summary of Last Generation ---\n")
        f.write(f"Timestamp: {now_vietnam.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        if not urls_summary:
            f.write("No new images were processed in this run.\n")
        else:
            for domain, counts in urls_summary.items():
                f.write(f"Domain: {domain}\n")
                f.write(f"  Processed Images: {counts['processed']}\n")
                f.write(f"  Skipped Images: {counts['skipped']}\n")
                f.write(f"  Total URLs Found: {counts['total_to_process']}\n\n")
    print(f"Generation summary saved to {log_file_path}")

if __name__ == "__main__":
    main()
