# 📖 HƯỚNG DẪN SỬ DỤNG HỆ THỐNG FBEM

**FBEM (Facebook Extension Crawler + MCP)** là giải pháp tự động hóa xuất bản nội dung (Reels, Hình ảnh / Album) và quản lý Fanpage/Profile trên Facebook bằng cách **mô phỏng 100% Native Web API của người dùng thật** qua Chrome Extension, kết nối trực tiếp với các AI Agent qua giao thức MCP (Model Context Protocol).

---

## 📑 MỤC LỤC

1. [Ưu điểm vượt trội](#1-ưu-điểm-vượt-trội)
2. [Yêu cầu hệ thống](#2-yêu-cầu-hệ-thống)
3. [Cài đặt môi trường](#3-cài-đặt-môi-trường)
4. [Khởi động FBEM Bridge](#4-khởi-động-fbem-bridge)
5. [Cài đặt Chrome Extension](#5-cài-đặt-chrome-extension)
6. [Cơ chế Bắt mẫu (Capture & Replay)](#6-cơ-chế-bắt-mẫu-capture--replay)
7. [Tích hợp vào AI Agent qua MCP](#7-tích-hợp-vào-ai-agent-qua-mcp)
8. [Danh sách các công cụ MCP (MCP Tools)](#8-danh-sách-các-công-cụ-mcp-mcp-tools)
9. [Xử lý sự cố thường gặp (Troubleshooting)](#9-xử-lý-sự-cố-thường-gặp-troubleshooting)

---

## 1. Ưu điểm vượt trội

- **Không bị bóp tương tác (No Reach Suppression):** FBEM không dùng Graph API (vốn thường bị thuật toán Facebook hạn chế lượt phân phối). FBEM gọi trực tiếp các API nội bộ mà giao diện web `facebook.com` sử dụng.
- **Cơ chế Tự phục hồi (Self-healing):** Hệ thống hoạt động theo nguyên lý bắt mẫu (*Passive Capture*). Khi Facebook thay đổi cấu trúc gói tin, bạn chỉ cần tự tay đăng 1 bài trên trình duyệt là hệ thống tự học mẫu mới mà không cần can thiệp mã nguồn.
- **Bảo mật tuyệt đối (Loopback-only):** Cổng kết nối WebSocket và HTTP chỉ lắng nghe trên máy cục bộ (`127.0.0.1`), không truyền token qua bên thứ ba.

---

## 2. Yêu cầu hệ thống

- **Hệ điều hành:** Windows, macOS hoặc Linux.
- **Python:** Phiên bản `>= 3.11` (Khuyên dùng công cụ `uv` để quản lý dependencies nhanh nhất).
- **Trình duyệt:** Google Chrome, Brave, Cốc Cốc hoặc Microsoft Edge.

---

## 3. Cài đặt môi trường

Mở terminal trong thư mục dự án `fbem`:

### Cách 1: Sử dụng `uv` (Khuyên dùng)
```bash
# Đồng bộ môi trường tự động theo file uv.lock
uv sync
```

### Cách 2: Sử dụng `venv` và `pip`
```bash
# Tạo môi trường ảo
python -m venv .venv

# Kích hoạt môi trường ảo:
# Trên Windows PowerShell:
.venv\Scripts\activate
# Trên Linux/macOS:
source .venv/bin/activate

# Cài đặt package ở chế độ editable
pip install -e .
```

---

## 4. Khởi động FBEM Bridge

Server Bridge đóng vai trò trung gian nhận lệnh từ AI Agent và giao tiếp với Chrome Extension qua WebSocket.

Chạy lệnh:
```bash
# Nếu dùng uv:
uv run python -m fbem.bridge

# Hoặc nếu dùng venv:
python -m fbem.bridge
# hoặc chạy trực tiếp lệnh:
fbem-bridge
```

Khi chạy thành công, Bridge sẽ lắng nghe:
- **HTTP API:** `http://127.0.0.1:47102`
- **WebSocket Extension:** `ws://127.0.0.1:9224/ws`

> ⚠️ **Lưu ý:** Giữ cửa sổ terminal này luôn chạy trong suốt quá trình tự động hóa.

---

## 5. Cài đặt Chrome Extension

1. Mở trình duyệt Chrome (nơi đã đăng nhập tài khoản Facebook của bạn).
2. Truy cập vào thanh địa chỉ: `chrome://extensions/`.
3. Bật công tắc **Developer mode (Chế độ dành cho nhà phát triển)** ở góc trên bên phải.
4. Bấm nút **Load unpacked (Tải tiện ích đã giải nén)**.
5. Chọn thư mục `extension` trong dự án FBEM.
6. Mở một tab `https://www.facebook.com` và giữ tab này luôn mở.

---

## 6. Cơ chế Bắt mẫu (Capture & Replay)

FBEM cần có **1 bài đăng mẫu đầu tiên** cho mỗi loại nội dung để ghi nhớ cấu trúc upload:

1. **Bắt mẫu Đăng Reels:**
   - Trên tab Facebook, tự tay đăng 1 video Reel ngắn bất kỳ.
   - Extension sẽ tự động bắt gói tin `rupload` và `ComposerStoryCreateMutation` rồi lưu vào `~/.fbem/captures/template.json`.
2. **Bắt mẫu Đăng Ảnh/Album:**
   - Tự tay đăng 1 bài viết kèm ảnh bất kỳ trên Facebook.
   - Extension sẽ tự động bắt cấu trúc upload ảnh.
3. **Kiểm tra trạng thái mẫu:**
   Mở terminal kiểm tra trạng thái:
   ```bash
   curl -s http://127.0.0.1:47102/api/health
   ```
   Nếu `has_template: true` (hoặc `has_photo_template: true`) tức là đã bắt mẫu thành công và sẵn sàng tự động hóa!

---

## 7. Tích hợp vào AI Agent qua MCP

### 7.1 Cấu hình cho Claude Desktop
Mở file `claude_desktop_config.json`:
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Thêm cấu hình:
```json
{
  "mcpServers": {
    "fbem": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\Users\\nguye\\Documents\\fbem",
        "run",
        "fbem-mcp"
      ]
    }
  }
}
```
*(Thay đường dẫn thư mục bằng đường dẫn thực tế trên máy bạn).*

### 7.2 Cấu hình cho Claude Code / Cursor / Cline / Antigravity
Chạy lệnh thêm MCP server:
```bash
claude mcp add fbem -- uv --directory /path/to/fbem run fbem-mcp
```
Hoặc trỏ trực tiếp vào file thực thi trong virtualenv:
```bash
/path/to/fbem/.venv/bin/fbem-mcp
```

---

## 8. Danh sách các công cụ MCP (MCP Tools)

Khi kết nối thành công, AI Agent có thể sử dụng các công cụ sau:

### 1. `post_reel`
Đăng một video ngắn Facebook Reel.
- **Tham số:**
  - `video_path` *(string, bắt buộc)*: Đường dẫn tuyệt đối đến file video `.mp4` trên máy.
  - `caption` *(string, tùy chọn)*: Nội dung mô tả kèm hashtag.
  - `page_id` *(string, tùy chọn)*: ID của Fanpage nếu muốn đăng dưới tư cách Page.
  - `scheduled_publish_time` *(int, tùy chọn)*: Thời gian hẹn giờ đăng (Epoch timestamp tính bằng giây).
  - `extension_id` *(string, tùy chọn)*: Chỉ định ID Chrome Profile thực hiện đăng nếu có nhiều nick kết nối.

### 2. `post_photos`
Đăng một bài viết có 1 ảnh hoặc Album nhiều ảnh.
- **Tham số:**
  - `image_paths` *(array of strings, bắt buộc)*: Danh sách đường dẫn file ảnh (`.jpg`, `.png`).
  - `caption` *(string, tùy chọn)*: Nội dung bài viết.
  - `page_id` *(string, tùy chọn)*: ID Fanpage cần đăng.
  - `scheduled_publish_time` *(int, tùy chọn)*: Thời gian hẹn giờ đăng.
  - `extension_id` *(string, tùy chọn)*: Chỉ định ID Chrome Profile thực hiện đăng.

### 3. `switch_profile`
Chuyển đổi ngữ cảnh làm việc của phiên duyệt sang Fanpage hoặc Profile khác.
- **Tham số:**
  - `target_id` *(string, bắt buộc)*: ID của Page hoặc Profile muốn chuyển sang.
  - `extension_id` *(string, tùy chọn)*: Chỉ định ID Chrome Profile cần chuyển đổi.

### 4. `get_identity`
Đọc thông tin ID và Tên của Profile/Fanpage mà tab trình duyệt hiện tại đang thao tác.
- **Tham số:**
  - `extension_id` *(string, tùy chọn)*: Chỉ định ID Chrome Profile cần đọc danh tính.

### 5. `health`
Kiểm tra tình trạng hoạt động của Bridge, Extension và tính khả dụng của phiên làm việc.

### 6. `capture_status`
Kiểm tra xem hệ thống đã bắt đủ mẫu Reel / Photo chưa và cung cấp hướng dẫn cụ thể nếu còn thiếu.

---

## 9. Xử lý sự cố thường gặp (Troubleshooting)

| Vấn đề | Nguyên nhân | Cách khắc phục |
| :--- | :--- | :--- |
| `extension_not_connected` | Chưa mở tab Facebook hoặc Extension chưa được tải | Kiểm tra `chrome://extensions`, đảm bảo Extension đang bật và đang mở ít nhất 1 tab `facebook.com`. |
| `no_template_captured` | Chưa từng đăng bài mẫu bằng tay | Tự tay đăng 1 Reel hoặc 1 bài Ảnh mẫu trên Facebook để Extension bắt gói tin. |
| Lỗi `story_create=null` hoặc `502` | Facebook đã cập nhật giao diện / API mới | Đăng lại 1 bài bằng tay trên trình duyệt để Extension ghi đè template mới. |
| `scheduledPublishTime invalid` | Truyền timestamp dạng mili-giây | Chuyển đổi timestamp về đơn vị **Giây** (10 chữ số, ví dụ `1787458800`). |

---

Chúc bạn tự động hóa Facebook thành công cùng **FBEM**! 🎉
