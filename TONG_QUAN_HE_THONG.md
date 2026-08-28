# ⚡ TỔNG QUAN HỆ THỐNG FBEM — HEADLESS FACEBOOK AUTOMATION & MCP

Tài liệu hướng dẫn toàn diện về kiến trúc, cấu hình kết nối Cloudflare Tunnel, bộ mẫu Template Native API có sẵn và danh mục 15 công cụ MCP để AI Agent điều khiển đăng bài tự động 100%.

---

## 📑 MỤC LỤC
1. [Kiến trúc vận hành](#1-kiến-trúc-vận-hành)
2. [Thông số kết nối & Hạ tầng](#2-thông-số-kết-nối--hạ-tầng)
3. [Dữ liệu & Template có sẵn](#3-dữ-liệu--template-có-sẵn)
4. [Danh mục 15 MCP Tools cho AI Agent](#4-danh-mục-15-mcp-tools-cho-ai-agent)
5. [Cấu hình AI Agent (Cursor / Claude Desktop)](#5-cấu-hình-ai-agent-cursor--claude-desktop)
6. [Các lệnh quản trị & Vận hành VPS](#6-các-lệnh-quản-trị--vận-hành-vps)

---

## 1. Kiến trúc vận hành

```
┌────────────────────────────────────────────────────────┐
│             MÁY TÍNH CÁ NHÂN (LOCAL PC)                │
│  - Trình duyệt Google Chrome mở tab facebook.com       │
│  - Tiện ích FBEM Chrome Extension                      │
│  - AI Agent (Cursor / Claude Desktop / Antigravity)    │
└───────────────────────────┬────────────────────────────┘
                            │
              (WSS / HTTPS qua Cloudflare)
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│             CLOUDFLARE ZERO TRUST TUNNEL               │
│  - REST API Domain : https://fb.shopcongngheso5.io.vn  │
│  - WebSocket Route : wss://fb.shopcongngheso5.io.vn/ws │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│             VPS LINUX (AWS 54.251.204.175)             │
│  - Docker Connector : cloudflare/cloudflared           │
│  - fbem-bridge       : FastAPI (:47102) + WS (:9224)   │
│  - SQLite Database  : Queue Engine, Accounts, History  │
│  - Template Store   : Native GraphQL & RUpload Models  │
└────────────────────────────────────────────────────────┘
```

---

## 2. Thông số kết nối & Hạ tầng

| Thành phần | Địa chỉ / Cổng kết nối | Mục đích sử dụng |
| :--- | :--- | :--- |
| **VPS IP** | `54.251.204.175` (Ubuntu 24.04 aarch64) | Máy chủ chạy Headless Daemon |
| **Cloudflare Domain** | `https://fb.shopcongngheso5.io.vn` | Public REST API & Webhook Sink |
| **Cloudflare WebSocket**| `wss://fb.shopcongngheso5.io.vn/ws` | Kênh kết nối thời gian thực với Extension |
| **Internal REST API** | `127.0.0.1:47102` (Swagger `/docs`) | Cổng backend nội bộ trên VPS |
| **Internal WS Server** | `127.0.0.1:9224` | Cổng WebSocket nội bộ trên VPS |

---

## 3. Dữ liệu & Template có sẵn

Hệ thống đã được đồng bộ sẵn toàn bộ mẫu cấu trúc Native API của Facebook vào `/home/ubuntu/.fbem/captures/template.json`:

1. **Mẫu Upload Video Reels (`rupload`)**:
   - Endpoint: `https://www.facebook.com/video/unified_cvc/`
   - Phương thức: `POST` (Tương thích Web Worker stream của Facebook Web).
2. **Mẫu Xuất bản Reels (`graphql`)**:
   - Mutation: `ComposerStoryCreateMutation`
   - Payload: Gắn video attachment, caption tùy chỉnh, hashtag, page ID và thời gian hẹn giờ (`scheduledPublishTime`).
3. **Mẫu Chuyển đổi Fanpage / Profile (`graphql_ops`)**:
   - Operation: `CometProfileSwitcherListQuery`
   - Quét và chuyển quyền quản trị nhanh chóng giữa các Fanpage và trang cá nhân.

---

## 4. Danh mục 15 MCP Tools cho AI Agent

Tất cả các công cụ dưới đây được tự động đăng ký trong MCP Server `fbem-mcp`:

### 🔹 Nhóm 1: Kiểm tra kết nối & Trạng thái
* **`health(extension_id?)`**: Kiểm tra trạng thái máy chủ, Chrome extension nào đang online, tab Facebook còn active không, thời gian TTL còn lại.
* **`capture_status(extension_id?)`**: Xem trạng thái mẫu template upload Reels/Ảnh đã sẵn sàng chưa.
* **`get_stats()`**: Xem thống kê tổng số tài khoản, số lượng job đang chờ, đang chạy, thành công trong ngày.

### 🔹 Nhóm 2: Quản lý Danh tính & Fanpage
* **`get_identity(extension_id?)`**: Lấy UID và tên của tài khoản / Fanpage đang đứng trên tab Facebook hiện tại.
* **`list_pages(extension_id?)`**: Lấy danh sách toàn bộ các Fanpage được tiện ích phát hiện.
* **`list_accounts(extension_id?)`**: Lấy danh sách tài khoản / Fanpage đã lưu trong cơ sở dữ liệu.
* **`switch_profile(target_id, extension_id?)`**: Chuyển quyền quản trị sang Fanpage mục tiêu theo Page ID.

### 🔹 Nhóm 3: Đăng bài & Media
* **`post_reel(video_path, caption, page_id?, scheduled_publish_time?, extension_id?)`**: Đăng ngay hoặc hẹn giờ 1 video Reels lên Facebook/Fanpage.
* **`post_photos(image_paths, caption, page_id?, scheduled_publish_time?, extension_id?)`**: Đăng bài viết kèm 1 hoặc nhiều hình ảnh.
* **`stage_media_from_url(url, filename?)`**: Tải video/ảnh từ URL trên mạng về thư mục media của server để chuẩn bị đăng.

### 🔹 Nhóm 4: Quản lý Hàng đợi & Lịch sử
* **`enqueue_job(kind, input_data, account_id?, delay_seconds?, run_at?)`**: Đẩy bài viết vào hàng đợi để hệ thống tự động đăng giãn cách (tránh checkpoint).
* **`list_jobs(status?, limit?)`**: Xem danh sách các job trong hàng đợi (`queued`, `running`, `completed`, `failed`).
* **`cancel_job(job_id)`**: Hủy 1 job đang ở trạng thái chờ.
* **`retry_job(job_id)`**: Chạy lại 1 job bị thất bại.
* **`get_history(limit?)`**: Xem lịch sử bài đã đăng, lấy permalink URL bài viết và video ID.

---

## 5. Cấu hình AI Agent (Cursor / Claude Desktop)

Thêm khối sau vào file cấu hình MCP của bạn:

### Với Cursor (`~/.cursor/mcp.json`):
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
      ],
      "env": {
        "FBEM_BRIDGE_URL": "https://fb.shopcongngheso5.io.vn"
      }
    }
  }
}
```

---

## 6. Các lệnh quản trị & Vận hành VPS

| Tác vụ | Lệnh thực hiện trên VPS |
| :--- | :--- |
| **Kiểm tra trạng thái Service** | `sudo systemctl status fbem` |
| **Xem log realtime của FBEM** | `sudo journalctl -u fbem -f` |
| **Khởi động lại Service** | `sudo systemctl restart fbem` |
| **Kiểm tra Cloudflare Tunnel** | `sudo docker ps` |
| **Xem log Cloudflare Tunnel** | `sudo docker logs -f fbem-cloudflared` |
| **Cập nhật code mới nhất** | `cd /home/ubuntu/fbem && git pull origin main && sudo systemctl restart fbem` |
