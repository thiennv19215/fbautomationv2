# 📖 HƯỚNG DẪN SỬ DỤNG HỆ THỐNG FBEM (Facebook Automation & MCP)

Chào mừng bạn đến với **FBEM** — Hệ thống tự động hóa đăng bài (Reels, Ảnh/Album), quản lý đa tài khoản Facebook (Via) & Fanpage theo cơ chế **Capture & Replay** mô phỏng 100% thao tác người dùng thật (Native Web API), không bị bóp tương tác như Graph API thông thường.

---

## 📑 MỤC LỤC
1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Cài đặt & Khởi động nhanh](#2-cài-đặt--khởi-động-nhanh)
3. [Cài đặt Chrome Extension](#3-cài-đặt-chrome-extension)
4. [Nguyên lý hoạt động (Capture & Replay)](#4-nguyên-lý-hoạt-động-capture--replay)
5. [Hướng dẫn sử dụng Giao diện Web (Dashboard)](#5-hướng-dẫn-sử-dụng-giao-diện-web-dashboard)
   - [5.1 Quản lý Tài khoản & Fanpage](#51-quản-lý-tài-khoản--fanpage)
   - [5.2 Quản lý Kho Media (Media Library)](#52-quản-lý-kho-media-media-library)
   - [5.3 Đăng bài & Lên lịch (Post & Schedule)](#53-đăng-bài--lên-lịch-post--schedule)
   - [5.4 Quản lý Hàng đợi & Lịch sử (Jobs Queue)](#54-quản-lý-hàng-đợi--lịch-sử-jobs-queue)
   - [5.5 Quản lý Chrome Extension](#55-quản-lý-chrome-extension)
6. [Tích hợp AI Agent qua MCP (Claude Desktop, Cursor, Claude Code)](#6-tích-hợp-ai-agent-qua-mcp)
7. [Các câu hỏi thường gặp & Xử lý sự cố (Troubleshooting)](#7-các-câu-hỏi-thường-gặp--xử-lý-sự-cố)

---

## 1. Yêu cầu hệ thống
- **Hệ điều hành**: Windows, macOS hoặc Linux.
- **Python**: Phiên bản `>= 3.10` (Khuyên dùng `uv` hoặc `venv`).
- **Node.js**: Phiên bản `>= 18` (để phát triển hoặc build frontend nếu cần).
- **Trình duyệt**: Google Chrome, Brave, CocCoc, hoặc Microsoft Edge.

---

## 2. Cài đặt & Khởi động nhanh

### Bước 1: Tải mã nguồn về máy
```bash
git clone https://github.com/thiennv19215/fbautomationv2.git fbem
cd fbem
```

### Bước 2: Tạo môi trường ảo và cài đặt thư viện Python
Sử dụng `uv` (khuyên dùng vì tốc độ cực nhanh):
```bash
# Cài uv nếu chưa có: pip install uv
uv venv
# Kích hoạt môi trường ảo:
# Trên Windows PowerShell:
.venv\Scripts\activate
# Trên Linux/macOS:
source .venv/bin/activate

# Cài đặt dependencies:
uv pip install -e .
```
*(Hoặc dùng `pip install -e .` nếu dùng python venv thông thường)*

### Bước 3: Khởi động Server Bridge (Backend + Dashboard)
```bash
python -m fbem.bridge
```
> Khi server chạy thành công, bạn sẽ thấy thông báo:
> - **API & Dashboard URL**: `http://127.0.0.1:47102/ui/`
> - **WebSocket Extension Port**: `ws://127.0.0.1:9224/ws`

👉 Truy cập vào trình duyệt: **[http://127.0.0.1:47102/ui/](http://127.0.0.1:47102/ui/)** để mở giao diện quản trị.

---

## 3. Cài đặt Chrome Extension

Extension đóng vai trò là "cầu nối" chạy trực tiếp trên phiên đăng nhập Facebook của bạn.

1. Mở trình duyệt Chrome (nơi đã đăng nhập tài khoản Facebook / Via của bạn).
2. Truy cập vào đường dẫn: `chrome://extensions/`
3. Bật công tắc **Developer mode (Chế độ dành cho nhà phát triển)** ở góc trên bên phải.
4. Bấm nút **Load unpacked (Tải tiện ích đã giải nén)**.
5. Chọn thư mục `extension` nằm trong thư mục dự án `fbem`.
6. Mở một tab Facebook (`https://www.facebook.com`) và đảm bảo tài khoản đã đăng nhập.
7. Mở Dashboard `http://127.0.0.1:47102/ui/` -> mục **Extensions**: bạn sẽ thấy tài khoản Facebook, tên Via, Avatar và danh sách Fanpage tự động xuất hiện!

> 💡 **Mẹo chạy nhiều tài khoản (Multi-Via):** Bạn có thể tạo nhiều **Chrome Profile** khác nhau, mỗi profile đăng nhập 1 nick Facebook và cài extension này. Hệ thống sẽ nhận diện từng profile với ID riêng biệt và hỗ trợ đăng song song!

---

## 4. Nguyên lý hoạt động (Capture & Replay)

FBEM sử dụng kỹ thuật **Passive Sniffing & Native Replay**:

1. **Lần đầu tiên (Capture - Bắt mẫu)**:
   - Bạn mở Facebook trên trình duyệt, tự tay đăng **1 video Reels** hoặc **1 bài ảnh** mẫu.
   - Extension sẽ tự động "bắt" cấu trúc gói tin upload (`rupload`, GraphQL mutations, token `fb_dtsg`) và lưu vào `template.json`.
2. **Các lần sau (Replay - Tự động hóa hoàn toàn)**:
   - Mỗi khi bạn bấm Đăng bài trên Dashboard hoặc ra lệnh qua AI Agent, FBEM sẽ tự động lấy video/ảnh mới + bóc tách token tươi của phiên duyệt hiện tại và gửi gói tin chuẩn khớp 100% với Facebook.
   - **Tác dụng**: Không bị thuật toán Facebook phạt giảm tương tác (như khi dùng Graph API của App), không lo checkpoint do hành vi lạ.
3. **Khi Facebook đổi giao diện / cập nhật payload**:
   - Chỉ cần tự tay đăng 1 bài trên trình duyệt để Extension ghi nhận template mới là hệ thống tự phục hồi ngay mà không cần sửa code!

---

## 5. Hướng dẫn sử dụng Giao diện Web (Dashboard)

Giao diện Dashboard tại `http://127.0.0.1:47102/ui/` bao gồm 6 phân hệ chính:

### 5.1 📊 Dashboard (Tổng quan)
- Hiển thị thống kê nhanh: Số lượng Via đang online, Tổng số Fanpage, Trạng thái các Jobs đang chạy, Dung lượng kho media.
- Biểu đồ tỷ lệ thành công theo thời gian thực.

### 5.2 📑 Fanpages & Accounts (Quản lý Fanpage & Tài khoản)
- **Tự động quét**: Khi Extension kết nối vào tài khoản Facebook, hệ thống tự động quét toàn bộ Fanpage mà Via đó đang quản lý và nhập vào danh sách.
- **Chế độ xem**:
  - **Dạng bảng (Table view)**: Tìm kiếm, lọc theo Niche/Category/Via, đổi tên, chỉnh sửa danh mục hoặc ghi chú.
  - **Gom nhóm theo Via (Grouped by Account)**: Xem từng nick Facebook đang nắm giữ những Page nào.
- **Thao tác hàng loạt (Bulk Actions)**: Chọn nhiều Fanpage cùng lúc để tạo bài đăng đồng loạt hoặc gắn nhãn.
- **Chuyển Profile**: Bấm nút chuyển trang nhanh để extension tự động switch ngữ cảnh sang Page đó.

### 5.3 📁 Media Library (Kho quản lý Media)
- **Tạo thư mục**: Phân loại media theo chiến dịch (ví dụ: `Reels_Hai_Huoc`, `Review_Phim`, `Product_Ads`).
- **Upload file**: Hỗ trợ tải lên video (`.mp4`, `.mov`) và hình ảnh (`.jpg`, `.png`).
- **Xem trước trực tiếp (Preview)**: Bấm vào video/ảnh để phát thử trực tiếp ngay trên Dashboard.
- **Tự động dọn dẹp (Auto-cleanup)**: Khi bài đăng thành công, hệ thống có thể tự động xóa file để tiết kiệm dung lượng ổ cứng.

### 5.4 ✍️ Post & Schedule (Đăng bài & Lên lịch)
1. **Chọn Fanpage đích**: Chọn 1 hoặc nhiều Fanpage cần đăng.
2. **Soạn nội dung (Caption)**: Hỗ trợ chèn biến linh hoạt như `{{account_name}}`, `{{date}}`.
3. **Bộ chọn Hashtag thông minh**: Bấm vào các hashtag gợi ý sẵn (`#reels`, `#viral`, `#trending`,...) để tự động nối vào caption.
4. **Chọn Media**: Chọn video/ảnh từ Kho Media hoặc tải lên file mới.
5. **Hình thức đăng**:
   - **Đăng ngay (Publish Now)**: Đưa vào hàng đợi và chạy lập tức.
   - **Lên lịch (Schedule)**: Chọn ngày giờ cụ thể để hệ thống tự động đăng đúng hẹn.

### 5.5 ⏳ Jobs & Queue (Tiến trình & Hàng đợi)
- Theo dõi toàn bộ lịch sử đăng bài với các trạng thái: `pending`, `running`, `success`, `failed`.
- Xem log chi tiết từng bước (Step-by-step: lấy token -> upload chunk -> publish story).
- Hỗ trợ nút **Hủy (Cancel)** hoặc **Thử lại (Retry)** cho các tác vụ thất bại.

### 5.6 🔌 Extensions (Quản lý kết nối)
- Hiển thị danh sách các Chrome Extension đang kết nối.
- Xem UID Facebook, tên hiển thị, avatar, và trạng thái template capture của từng profile.

---

## 6. Tích hợp AI Agent qua MCP

FBEM đi kèm một **MCP Server** (`Model Context Protocol`) chuẩn, cho phép các AI Agent như **Claude Desktop**, **Claude Code**, **Cursor**, **Antigravity** điều khiển trực tiếp việc đăng bài.

### Cấu hình `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "fbem": {
      "command": "python",
      "args": ["-m", "fbem.mcp"]
    }
  }
}
```

### Các công cụ AI (MCP Tools) hỗ trợ:
- `post_reel(media_path, caption, target_page_id)`: Đăng 1 video Reels lên Facebook/Fanpage.
- `post_photos(media_paths, caption, target_page_id)`: Đăng 1 hoặc nhiều ảnh kèm bài viết.
- `switch_profile(target_id)`: Chuyển ngữ cảnh quản trị sang Page khác.
- `get_identity()`: Lấy thông tin tài khoản Facebook đang đăng nhập hiện tại.
- `capture_status()`: Kiểm tra trạng thái đã có template upload hay chưa.
- `health()`: Kiểm tra kết nối giữa Agent -> Bridge -> Extension.

---

## 7. Các câu hỏi thường gặp & Xử lý sự cố

#### Q1: Tại sao Extension báo trạng thái Disconnected (Mất kết nối)?
> **Xử lý**: Đảm bảo bạn đã chạy lệnh `python -m fbem.bridge` ở terminal. Kiểm tra xem port `9224` có bị phần mềm diệt virus hoặc tường lửa chặn không.

#### Q2: Báo lỗi "No template captured for kind: reel"?
> **Xử lý**: Hệ thống chưa có mẫu cấu trúc đăng bài. Hãy mở tab Facebook trên trình duyệt và tự tay đăng 1 video Reel thử nghiệm. Sau khi đăng xong, Extension sẽ tự ghi nhận mẫu và các lần sau sẽ tự động 100%.

#### Q3: Làm sao để sửa giao diện Frontend hoặc build lại?
> Thư mục `frontend/` chứa mã nguồn React + Vite.
> ```bash
> cd frontend
> npm install
> npm run build   # Build file tĩnh tự động vào fbem/bridge/static
> ```

---

Chúc bạn có trải nghiệm tự động hóa tuyệt vời với FBEM! Nếu có thắc mắc, vui lòng tạo issue hoặc đóng góp qua pull request. 🚀
