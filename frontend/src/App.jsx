import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, BookOpen, Bot, CheckCircle2, ChevronRight, CircleAlert, Clock3,
  Copy, ExternalLink, Eye, Film, Flag, FolderTree, GalleryVerticalEnd, Hash,
  History, Images, Layers, LayoutDashboard, Menu, MonitorDot, Pencil, Plus,
  RefreshCw, RotateCcw, Search, Server, Settings2, ShieldCheck, Sparkles,
  Timer, Trash2, User, UserRound, UsersRound, X,
} from "lucide-react";
import { endpoints } from "./api";

const NAV = [
  ["overview", "Tổng quan", LayoutDashboard],
  ["accounts", "Quản lý Fanpage", Flag],
  ["extensions", "Chrome Profile", MonitorDot],
  ["media", "Thư viện Video", FolderTree],
  ["scripts", "Thư viện kịch bản", BookOpen],
  ["jobs", "Hàng đợi & Tiến độ", History],
];

const TITLES = {
  overview: ["Tổng quan", "Theo dõi toàn bộ hệ thống xuất bản và tiến độ thời gian thực"],
  accounts: ["Quản lý Fanpage", "Danh sách toàn bộ Fanpage, liên kết Nick Via và tạo bài đăng hàng loạt"],
  extensions: ["Chrome Profile & Via", "Theo dõi và đồng bộ các phiên trình duyệt Facebook đang kết nối"],
  media: ["Thư viện Video & Media", "Tạo thư mục, tải lên file và phân loại video Reel cho từng dàn Fanpage"],
  scripts: ["Thư viện kịch bản", "Tạo nội dung một lần, vận hành trên nhiều tài khoản"],
  jobs: ["Hàng đợi & Tiến độ", "Kiểm soát chi tiết tiến độ thực thi từng bước và kết quả"],
};

const KIND_LABEL = {
  post_reel: "Đăng Reel",
  post_photos: "Ảnh / Album",
  switch_profile: "Chuyển profile",
  get_identity: "Đọc danh tính",
};

const STATUS_LABEL = {
  succeeded: "Thành công",
  running: "Đang chạy",
  queued: "Đang chờ",
  waiting_connection: "Chờ kết nối",
  failed: "Thất bại",
  cancelled: "Đã hủy",
};

function Badge({ status }) {
  const icon =
    status === "succeeded" || status === "online" ? (
      <CheckCircle2 />
    ) : status === "failed" || status === "offline" ? (
      <CircleAlert />
    ) : status === "running" ? (
      <RefreshCw className="spin" />
    ) : (
      <Clock3 />
    );
  return (
    <span className={`badge badge-${status}`}>
      {icon}
      {STATUS_LABEL[status] ||
        (status === "online" ? "Trực tuyến" : status === "offline" ? "Ngoại tuyến" : status)}
    </span>
  );
}

function Empty({ icon: Icon = Sparkles, title, text, action }) {
  return (
    <div className="empty">
      <div className="empty-icon"><Icon /></div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}

function Modal({ title, subtitle, children, onClose }) {
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-head">
          <div>
            <h2>{title}</h2>
            <p>{subtitle}</p>
          </div>
          <button className="icon-button" onClick={onClose}><X /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Stat({ icon: Icon, label, value, helper, tone }) {
  return (
    <article className="stat-card">
      <div className={`stat-icon ${tone}`}><Icon /></div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{helper}</small>
      </div>
    </article>
  );
}

function relativeTime(epoch) {
  if (!epoch) return "Chưa có";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (seconds < 60) return "Vừa xong";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} phút trước`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} giờ trước`;
  return new Date(epoch * 1000).toLocaleDateString("vi-VN");
}

function formatFullTime(epoch) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

function formatDuration(startedAt, finishedAt, status) {
  if (!startedAt) return "—";
  const end = finishedAt || (status === "running" ? Date.now() / 1000 : null);
  if (!end) return "—";
  const diff = Math.max(0, Math.round(end - startedAt));
  if (diff < 60) return `${diff}s`;
  const mins = Math.floor(diff / 60);
  const secs = diff % 60;
  return `${mins}m ${secs}s`;
}

function computeJobProgress(job) {
  switch (job.status) {
    case "queued":
      return { percent: 15, stage: "Đã xếp hàng", tone: "queued" };
    case "waiting_connection":
      return { percent: 25, stage: "Chờ Chrome Profile online", tone: "waiting" };
    case "running":
      return { percent: 70, stage: "Đang tải & xuất bản...", tone: "running" };
    case "succeeded":
      return { percent: 100, stage: "Hoàn tất thành công", tone: "succeeded" };
    case "failed":
      return { percent: 100, stage: "Thực thi thất bại", tone: "failed" };
    case "cancelled":
      return { percent: 100, stage: "Đã hủy bỏ", tone: "cancelled" };
    default:
      return { percent: 0, stage: "Chưa xác định", tone: "queued" };
  }
}

function getPipelineSteps(job) {
  const isMedia = ["post_reel", "post_photos"].includes(job.kind);
  const isSwitch = job.kind === "switch_profile";

  const s1 = {
    title: "1. Khởi tạo & Xếp hàng",
    desc: job.created_at ? formatFullTime(job.created_at) : "Chờ xếp hàng",
    status: "completed",
  };

  let s2 = {
    title: "2. Định tuyến & Xác thực Page",
    desc: "Kiểm tra quyền & đồng bộ token",
    status: "pending",
  };

  let s3 = {
    title: isMedia ? "3. Tải & Đóng gói Media" : "3. Xử lý Payload",
    desc: isMedia ? "Tải lên rupload Facebook" : "Kiểm tra tham số",
    status: "pending",
  };

  let s4 = {
    title: isSwitch ? "4. Đổi Profile Session" : "4. Xuất bản GraphQL",
    desc: isSwitch ? "CometProfileSwitchMutation" : "ComposerStoryCreateMutation",
    status: "pending",
  };

  let s5 = {
    title: "5. Hoàn tất & Trả kết quả",
    desc: job.finished_at ? formatFullTime(job.finished_at) : "Đang chờ hoàn tất",
    status: "pending",
  };

  if (job.status === "queued" || job.status === "waiting_connection") {
    s2.status = "pending";
    s3.status = "pending";
    s4.status = "pending";
    s5.status = "pending";
  } else if (job.status === "running") {
    s2.status = "completed";
    s3.status = "active";
    s4.status = "active";
    s5.status = "pending";
  } else if (job.status === "succeeded") {
    s2.status = "completed";
    s3.status = "completed";
    s4.status = "completed";
    s5.status = "completed";
  } else if (job.status === "failed") {
    s2.status = job.started_at ? "completed" : "failed";
    s3.status = job.started_at ? "failed" : "pending";
    s4.status = "pending";
    s5.status = "failed";
    s5.desc = job.error || "Thất bại";
  } else if (job.status === "cancelled") {
    s5.status = "failed";
    s5.desc = "Tác vụ đã bị hủy";
  }

  return [s1, s2, s3, s4, s5];
}

export default function App() {
  const [view, setView] = useState("overview");
  const [data, setData] = useState({
    extensions: [],
    accounts: [],
    scripts: [],
    jobs: [],
    health: {},
  });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState(null);
  const [modal, setModal] = useState(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [mobileNav, setMobileNav] = useState(false);

  const load = useCallback(async (quiet = false) => {
    quiet ? setRefreshing(true) : setLoading(true);
    try {
      const [extensions, accounts, scripts, jobs, health] = await Promise.all([
        endpoints.extensions(),
        endpoints.accounts(),
        endpoints.scripts(),
        endpoints.jobs(),
        endpoints.health(),
      ]);
      setData({
        extensions: extensions.items,
        accounts: accounts.items,
        scripts: scripts.items,
        jobs: jobs.items,
        health,
      });
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(() => load(true), 4000);
    return () => clearInterval(timer);
  }, [load]);

  const notify = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const accountMap = useMemo(
    () => Object.fromEntries(data.accounts.map((a) => [a.id, a])),
    [data.accounts]
  );
  const extensionMap = useMemo(
    () => Object.fromEntries(data.extensions.map((e) => [e.id, e])),
    [data.extensions]
  );

  const jobsOpen = data.jobs.filter((j) =>
    ["queued", "running", "waiting_connection"].includes(j.status)
  );
  const succeededToday = data.jobs.filter(
    (j) => j.status === "succeeded" && j.finished_at > Date.now() / 1000 - 86400
  ).length;

  const totalCompleted = data.jobs.filter((j) =>
    ["succeeded", "failed", "cancelled"].includes(j.status)
  ).length;
  const successRate =
    totalCompleted > 0
      ? Math.round(
          (data.jobs.filter((j) => j.status === "succeeded").length / totalCompleted) * 100
        )
      : 100;

  const filteredJobs = useMemo(() => {
    return data.jobs.filter((j) => {
      const matchesQuery = `${accountMap[j.account_id]?.name || ""} ${j.kind} ${j.status} ${j.id}`
        .toLowerCase()
        .includes(query.toLowerCase());
      if (!matchesQuery) return false;
      if (statusFilter === "all") return true;
      if (statusFilter === "running") return j.status === "running";
      if (statusFilter === "queued") return ["queued", "waiting_connection"].includes(j.status);
      if (statusFilter === "succeeded") return j.status === "succeeded";
      if (statusFilter === "failed") return ["failed", "cancelled"].includes(j.status);
      return true;
    });
  }, [data.jobs, accountMap, query, statusFilter]);

  const submit = async (action, body, success) => {
    try {
      await action(body);
      setModal(null);
      notify(success);
      await load(true);
    } catch (e) {
      notify(e.message, "error");
    }
  };

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><GalleryVerticalEnd /></div>
          <div>
            <b>FBEM</b>
            <span>Control Center</span>
          </div>
        </div>
        <nav>
          {NAV.map(([id, label, Icon]) => (
            <button
              key={id}
              className={view === id ? "active" : ""}
              onClick={() => {
                setView(id);
                setMobileNav(false);
              }}
            >
              <Icon />
              <span>{label}</span>
              {view === id && <ChevronRight />}
            </button>
          ))}
        </nav>
        <div className="sidebar-status">
          <span className={data.health.extension_connected ? "pulse" : "pulse offline"} />
          <div>
            <b>{data.health.extension_connected ? "Bridge & Extension Online" : "Mất kết nối"}</b>
            <span>127.0.0.1:47102</span>
          </div>
        </div>
      </aside>

      {mobileNav && <button className="nav-overlay" onClick={() => setMobileNav(false)} />}

      <main>
        <header>
          <div className="header-title">
            <button className="mobile-menu" onClick={() => setMobileNav(true)}><Menu /></button>
            <div>
              <h1>{TITLES[view][0]}</h1>
              <p>{TITLES[view][1]}</p>
            </div>
          </div>
          <div className="header-actions">
            <div className="live-pill">
              <span className={data.extensions.some((x) => x.connected !== false) ? "" : "offline"} />
              {data.extensions.filter((x) => x.connected !== false).length} Via Online
            </div>

            <button
              className="secondary header-btn"
              onClick={() => setModal({ type: "account" })}
              title="Thêm Fanpage mới do Nick Via quản trị"
            >
              <Plus style={{ width: "15px", height: "15px" }} /> Thêm Fanpage
            </button>

            <button
              className="primary header-btn"
              onClick={() => setModal({ type: "job" })}
              title="Tạo chiến dịch đăng Reel / Ảnh hàng loạt"
            >
              <Layers style={{ width: "15px", height: "15px" }} /> Tạo bài đăng
            </button>

            <button
              className="icon-button refresh"
              onClick={() => load(true)}
              title="Làm mới dữ liệu toàn hệ thống"
            >
              <RefreshCw className={refreshing ? "spin" : ""} />
            </button>
            <button className="avatar" title="Quản trị viên">AD</button>
          </div>
        </header>

        {error && (
          <div className="error-banner">
            <CircleAlert />
            Không thể tải dữ liệu: {error}
            <button onClick={() => load()}>Thử lại</button>
          </div>
        )}

        <div className="content">
          {loading ? (
            <Loading />
          ) : (
            <>
              {view === "overview" && (
                <Overview
                  data={data}
                  accountMap={accountMap}
                  jobsOpen={jobsOpen}
                  succeededToday={succeededToday}
                  successRate={successRate}
                  setView={setView}
                  setModal={setModal}
                />
              )}
              {view === "accounts" && (
                <Accounts
                  accounts={data.accounts}
                  extensions={data.extensions}
                  extensionMap={extensionMap}
                  scripts={data.scripts}
                  jobs={data.jobs}
                  setModal={setModal}
                  notify={notify}
                  reload={() => load(true)}
                />
              )}
              {view === "extensions" && (
                <Extensions
                  extensions={data.extensions}
                  accounts={data.accounts}
                  setModal={setModal}
                  notify={notify}
                  reload={() => load(true)}
                />
              )}
              {view === "media" && (
                <MediaLibrary
                  accounts={data.accounts}
                  scripts={data.scripts}
                  setModal={setModal}
                  notify={notify}
                />
              )}
              {view === "scripts" && <Scripts scripts={data.scripts} accounts={data.accounts} setModal={setModal} />}
              {view === "jobs" && (
                <Jobs
                  jobs={filteredJobs}
                  allJobs={data.jobs}
                  accountMap={accountMap}
                  query={query}
                  setQuery={setQuery}
                  statusFilter={statusFilter}
                  setStatusFilter={setStatusFilter}
                  setModal={setModal}
                  onCancel={async (id) => {
                    try {
                      await endpoints.cancelJob(id);
                      notify("Đã hủy tác vụ");
                      load(true);
                    } catch (e) {
                      notify(e.message, "error");
                    }
                  }}
                  onRetry={async (id) => {
                    try {
                      await endpoints.retryJob(id);
                      notify("Đã đưa tác vụ chạy lại vào hàng đợi");
                      load(true);
                    } catch (e) {
                      notify(e.message, "error");
                    }
                  }}
                />
              )}
            </>
          )}
        </div>
      </main>

      {(modal === "account" || modal?.type === "account") && (
        <AccountModal
          extensions={data.extensions}
          account={modal?.account}
          scripts={data.scripts}
          defaultExtensionId={modal?.defaultExtensionId}
          close={() => setModal(null)}
          submit={(body) =>
            modal?.account
              ? submit(
                  (value) => endpoints.updateAccount(modal.account.id, value),
                  body,
                  "Đã cập nhật Fanpage"
                )
              : submit(endpoints.createAccount, body, "Đã thêm Fanpage")
          }
        />
      )}

      {(modal === "script" || modal?.type === "script") && (
        <ScriptModal
          script={modal?.script}
          close={() => setModal(null)}
          submit={(body) =>
            modal?.script
              ? submit(
                  (value) => endpoints.updateScript(modal.script.id, value),
                  body,
                  "Đã cập nhật kịch bản"
                )
              : submit(endpoints.createScript, body, "Đã lưu kịch bản")
          }
        />
      )}

      {modal?.type === "job" && (
        <JobModal
          accounts={data.accounts}
          scripts={data.scripts}
          initialAccount={modal.accountId}
          initialAccountIds={modal.initialAccountIds}
          initialVideoUrl={modal.initialVideoUrl}
          initialKind={modal.initialKind}
          close={() => setModal(null)}
          submit={async (body) => {
            const bulk = body.accountIds?.length > 1;
            await submit(
              bulk ? endpoints.createBulkJobs : endpoints.createJob,
              bulk ? body : { ...body, accountId: body.accountIds[0], accountIds: undefined },
              bulk ? `Đã tạo ${body.accountIds.length} tác vụ` : "Đã đưa tác vụ vào hàng đợi"
            );
          }}
        />
      )}

      {modal?.type === "createFolder" && (
        <CreateFolderModal
          close={() => setModal(null)}
          onDone={modal.onDone}
          notify={notify}
        />
      )}

      {modal?.type === "uploadMedia" && (
        <UploadMediaModal
          folders={modal.folders}
          activeFolder={modal.activeFolder}
          close={() => setModal(null)}
          onDone={modal.onDone}
          notify={notify}
        />
      )}

      {modal?.type === "videoPreview" && (
        <VideoPreviewModal
          item={modal.item}
          close={() => setModal(null)}
          setModal={setModal}
        />
      )}

      {modal?.type === "job-detail" && (
        <JobDetail
          job={modal.job}
          account={accountMap[modal.job.account_id]}
          close={() => setModal(null)}
          retry={async () => {
            await endpoints.retryJob(modal.job.id);
            setModal(null);
            notify("Đã đưa tác vụ chạy lại vào hàng đợi");
            load(true);
          }}
        />
      )}

      {modal?.type === "confirm" && (
        <ConfirmModal
          {...modal}
          close={() => setModal(null)}
          confirm={async () => {
            try {
              await modal.action();
              setModal(null);
              notify(modal.success);
              load(true);
            } catch (e) {
              notify(e.message, "error");
            }
          }}
        />
      )}

      {toast && (
        <div className={`toast ${toast.type}`}>
          <span>{toast.type === "success" ? <CheckCircle2 /> : <CircleAlert />}</span>
          {toast.message}
        </div>
      )}
    </div>
  );
}

function Loading() {
  return (
    <div className="loading-grid">
      {Array.from({ length: 8 }).map((_, i) => (
        <div className="skeleton" key={i} />
      ))}
    </div>
  );
}

function Overview({
  data,
  accountMap,
  jobsOpen,
  succeededToday,
  successRate,
  setView,
  setModal,
}) {
  const recent = data.jobs.slice(0, 6);
  const runningJobs = data.jobs.filter((j) => j.status === "running");

  return (
    <>
      <section className="stats-grid">
        <Stat
          icon={UsersRound}
          label="Tổng tài khoản"
          value={data.accounts.length}
          helper={`${data.accounts.filter((a) => a.enabled).length} đang hoạt động`}
          tone="blue"
        />
        <Stat
          icon={MonitorDot}
          label="Extension Online"
          value={data.extensions.length}
          helper={`${data.extensions.filter((e) => e.busy).length} đang bận`}
          tone="violet"
        />
        <Stat
          icon={Activity}
          label="Tác vụ đang mở"
          value={jobsOpen.length}
          helper={`${runningJobs.length} đang thực thi trực tiếp`}
          tone="amber"
        />
        <Stat
          icon={CheckCircle2}
          label="Tỷ lệ thành công"
          value={`${successRate}%`}
          helper={`${succeededToday} bài thành công hôm nay`}
          tone="green"
        />
      </section>

      {/* Real-time System Progress Tracker */}
      <section className="panel progress-overview-panel">
        <div className="panel-head">
          <div>
            <h2>Tiến độ thực thi hàng đợi toàn hệ thống</h2>
            <p>Theo dõi luồng xử lý và các tác vụ đang chạy trong thời gian thực</p>
          </div>
          <button className="text-button" onClick={() => setView("jobs")}>
            Xem toàn bộ hàng đợi <ChevronRight />
          </button>
        </div>

        <div className="system-progress-body">
          <div className="progress-summary-bar">
            <div className="progress-summary-info">
              <span>
                Trạng thái: <b>{runningJobs.length > 0 ? `Đang chạy ${runningJobs.length} tác vụ song song` : jobsOpen.length > 0 ? "Đang chờ điều phối" : "Hàng đợi rảnh"}</b>
              </span>
              <span>
                {data.jobs.length > 0 ? `${data.jobs.filter((j) => j.status === "succeeded").length}/${data.jobs.length} tác vụ thành công` : "0 tác vụ"}
              </span>
            </div>
            <div className="progress-bar-wrap big">
              <div
                className="progress-bar-fill succeeded"
                style={{
                  width: `${data.jobs.length > 0 ? (data.jobs.filter((j) => j.status === "succeeded").length / data.jobs.length) * 100 : 0}%`,
                }}
              />
              <div
                className="progress-bar-fill running"
                style={{
                  width: `${data.jobs.length > 0 ? (runningJobs.length / data.jobs.length) * 100 : 0}%`,
                }}
              />
            </div>
          </div>

          {runningJobs.length > 0 && (
            <div className="active-jobs-stream">
              {runningJobs.map((j) => {
                const prog = computeJobProgress(j);
                const duration = formatDuration(j.started_at, j.finished_at, j.status);
                return (
                  <div className="active-job-card" key={j.id}>
                    <div className="active-job-header">
                      <div className="job-kind">
                        <div><Bot /></div>
                        <span>
                          <b>{KIND_LABEL[j.kind] || j.kind}</b>
                          <small>{accountMap[j.account_id]?.name || "Tài khoản"}</small>
                        </span>
                      </div>
                      <div className="active-job-timer">
                        <Timer /> {duration}
                      </div>
                    </div>
                    <div className="progress-bar-wrap">
                      <div className="progress-bar-fill running" style={{ width: `${prog.percent}%` }} />
                    </div>
                    <div className="active-job-stage">
                      <span>{prog.stage}</span>
                      <small>{prog.percent}%</small>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </section>

      <section className="overview-grid">
        <div className="panel account-panel">
          <div className="panel-head">
            <div>
              <h2>Tình trạng tài khoản</h2>
              <p>Phiên và hàng đợi theo từng tài khoản</p>
            </div>
            <button className="text-button" onClick={() => setView("accounts")}>
              Xem tất cả <ChevronRight />
            </button>
          </div>
          {data.accounts.length ? (
            <div className="account-list">
              {data.accounts.slice(0, 5).map((a) => {
                const ext = data.extensions.find((e) => e.id === a.extension_id);
                const queue = jobsOpen.filter((j) => j.account_id === a.id);
                return (
                  <div className="account-row" key={a.id}>
                    <div className="account-avatar">{a.name.slice(0, 2).toUpperCase()}</div>
                    <div className="account-name">
                      <b>{a.name}</b>
                      <span>ID {a.facebook_id}</span>
                    </div>
                    <Badge status={ext ? "online" : "offline"} />
                    <div className="queue-count">
                      <b>{queue.length}</b>
                      <span>tác vụ</span>
                    </div>
                    <button
                      className="icon-button"
                      onClick={() => setModal({ type: "job", accountId: a.id })}
                    >
                      <Plus />
                    </button>
                  </div>
                );
              })}
            </div>
          ) : (
            <Empty
              icon={UserRound}
              title="Chưa có tài khoản"
              text="Kết nối extension và thêm tài khoản đầu tiên."
              action={
                <button className="primary" onClick={() => setModal("account")}>
                  <Plus /> Thêm tài khoản
                </button>
              }
            />
          )}
        </div>

        <div className="panel">
          <div className="panel-head">
            <div>
              <h2>Hoạt động gần đây</h2>
              <p>Cập nhật theo thời gian thực</p>
            </div>
            <button className="text-button" onClick={() => setView("jobs")}>
              Lịch sử <ChevronRight />
            </button>
          </div>
          {recent.length ? (
            <div className="activity-list">
              {recent.map((j) => (
                <div className="activity-row" key={j.id}>
                  <div className={`activity-icon ${j.status}`}><Bot /></div>
                  <div>
                    <b>{KIND_LABEL[j.kind] || j.kind}</b>
                    <span>
                      {accountMap[j.account_id]?.name || "Tài khoản"} · {relativeTime(j.created_at)}
                    </span>
                  </div>
                  <Badge status={j.status} />
                </div>
              ))}
            </div>
          ) : (
            <Empty
              icon={History}
              title="Chưa có hoạt động"
              text="Các tác vụ mới sẽ xuất hiện tại đây."
            />
          )}
        </div>
      </section>
    </>
  );
}

function Accounts({ accounts, extensions, extensionMap, scripts = [], jobs, setModal, notify, reload }) {
  const [search, setSearch] = useState("");
  const [statusTab, setStatusTab] = useState("all"); // 'all', 'online', 'offline', 'has_pages'
  const [viewMode, setViewMode] = useState("list");
  const [selectedPageIds, setSelectedPageIds] = useState([]);
  const [scanning, setScanning] = useState(null);
  const [scanningPages, setScanningPages] = useState(null);
  const [scanningAll, setScanningAll] = useState(false);

  const scriptMap = useMemo(
    () => Object.fromEntries(scripts.map((s) => [s.id, s])),
    [scripts]
  );

  // Group accounts by extension ID
  const viaGroups = useMemo(() => {
    const map = {};
    for (const ext of extensions) {
      map[ext.id] = { ext, isOnline: ext.connected !== false, pages: [] };
    }
    for (const acc of accounts) {
      const extId = acc.extension_id || "legacy";
      if (!map[extId]) {
        map[extId] = {
          ext: extensionMap[extId] || { id: extId, fbUser: null, connected: false },
          isOnline: false,
          pages: [],
        };
      }
      map[extId].pages.push(acc);
    }
    return Object.values(map);
  }, [accounts, extensions, extensionMap]);

  // Master flat pages list with computed meta
  const masterPages = useMemo(() => {
    return accounts.map((acc) => {
      const ext = extensionMap[acc.extension_id] || { id: acc.extension_id || "legacy", connected: false };
      const isOnline = ext.connected !== false && Boolean(ext.id);
      const activeJobs = jobs.filter(
        (j) => j.account_id === acc.id && ["queued", "running", "waiting_connection"].includes(j.status)
      );
      return {
        ...acc,
        ext,
        isOnline,
        activeJobs,
      };
    });
  }, [accounts, extensionMap, jobs]);

  // Filtered flat pages
  const filteredPages = useMemo(() => {
    return masterPages.filter((p) => {
      if (statusTab === "online" && !p.isOnline) return false;
      if (statusTab === "offline" && p.isOnline) return false;
      if (statusTab === "enabled" && !p.enabled) return false;
      if (statusTab === "disabled" && p.enabled) return false;

      if (!search.trim()) return true;
      const q = search.toLowerCase().trim();
      return (
        (p.name || "").toLowerCase().includes(q) ||
        String(p.facebook_id || "").toLowerCase().includes(q) ||
        (p.notes || "").toLowerCase().includes(q) ||
        (p.assigned_folder || "").toLowerCase().includes(q) ||
        (p.ext.fbUser?.name || "").toLowerCase().includes(q) ||
        String(p.ext.fbUser?.id || "").toLowerCase().includes(q) ||
        (p.ext.id || "").toLowerCase().includes(q)
      );
    });
  }, [masterPages, search, statusTab]);

  // Filtered groups
  const filteredGroups = useMemo(() => {
    return viaGroups.filter((g) => {
      if (statusTab === "online" && !g.isOnline) return false;
      if (statusTab === "offline" && g.isOnline) return false;
      if (statusTab === "has_pages" && g.pages.length === 0) return false;

      if (!search.trim()) return true;
      const q = search.toLowerCase().trim();
      const viaName = (g.ext.fbUser?.name || "").toLowerCase();
      const viaUid = String(g.ext.fbUser?.id || "").toLowerCase();
      const extId = (g.ext.id || "").toLowerCase();

      if (viaName.includes(q) || viaUid.includes(q) || extId.includes(q)) return true;

      return g.pages.some(
        (p) =>
          (p.name || "").toLowerCase().includes(q) ||
          String(p.facebook_id || "").toLowerCase().includes(q) ||
          (p.notes || "").toLowerCase().includes(q)
      );
    });
  }, [viaGroups, search, statusTab]);

  const onlineVias = useMemo(() => viaGroups.filter((g) => g.isOnline), [viaGroups]);
  const totalPagesCount = accounts.length;
  const activeQueuedJobs = jobs.filter((j) =>
    ["queued", "running", "waiting_connection"].includes(j.status)
  ).length;

  const scanIdentity = async (extensionId) => {
    setScanning(extensionId);
    try {
      const res = await endpoints.extensionIdentity(extensionId);
      if (res && res.id) {
        notify(`Tab hiện tại: ${res.name || "FB User"} (${res.id})`);
        reload();
      } else {
        notify("Không phát hiện tab Facebook nào đang mở trên Nick này.", "error");
      }
    } catch (e) {
      notify(e.message, "error");
    } finally {
      setScanning(null);
    }
  };

  const scanAllPages = async (extensionId) => {
    setScanningPages(extensionId);
    try {
      const res = await endpoints.scanPages(extensionId);
      if (res && res.created >= 0) {
        notify(`Quét xong! Phát hiện ${res.found} Page, đã thêm mới ${res.created} Page.`);
        reload();
      }
    } catch (e) {
      notify(e.message, "error");
    } finally {
      setScanningPages(null);
    }
  };

  const scanAllOnlineVias = async () => {
    if (onlineVias.length === 0) {
      notify("Không có Nick Via nào đang online để quét.", "error");
      return;
    }
    setScanningAll(true);
    let totalFound = 0;
    let totalCreated = 0;
    try {
      for (const group of onlineVias) {
        try {
          const res = await endpoints.scanPages(group.ext.id);
          if (res) {
            totalFound += res.found || 0;
            totalCreated += res.created || 0;
          }
        } catch (err) {
          console.warn("Scan failed for via:", group.ext.id, err);
        }
      }
      notify(`Đã quét toàn bộ Nick Via online: Tìm thấy ${totalFound} Page, thêm mới ${totalCreated} Page.`);
      reload();
    } finally {
      setScanningAll(false);
    }
  };

  const togglePageSelect = (id) => {
    setSelectedPageIds((curr) =>
      curr.includes(id) ? curr.filter((x) => x !== id) : [...curr, id]
    );
  };

  const toggleSelectAll = () => {
    if (selectedPageIds.length === filteredPages.length) {
      setSelectedPageIds([]);
    } else {
      setSelectedPageIds(filteredPages.map((p) => p.id));
    }
  };

  const toggleAccountEnabled = async (acc) => {
    try {
      await endpoints.updateAccount(acc.id, {
        name: acc.name,
        facebookId: acc.facebook_id,
        extensionId: acc.extension_id,
        accountType: acc.account_type || "page",
        parentId: acc.parent_id || null,
        notes: acc.notes || "",
        assignedFolder: acc.assigned_folder,
        defaultScriptId: acc.default_script_id,
        enabled: !acc.enabled,
      });
      notify(`Đã ${!acc.enabled ? "bật" : "tắt"} Fanpage ${acc.name}`);
      reload();
    } catch (e) {
      notify(e.message, "error");
    }
  };

  const copyId = (id, label = "ID") => {
    navigator.clipboard?.writeText(String(id));
    notify(`Đã sao chép ${label}: ${id}`);
  };

  return (
    <>
      <PageToolbar
        title="Quản lý Danh sách Fanpage & Nick Via"
        subtitle="Danh sách toàn bộ các Fanpage được quản lý, phân loại theo Nick Via và điều phối xuất bản tự động."
        action={
          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" }}>
            <div className="view-mode-toggle">
              <button
                type="button"
                className={`view-mode-btn ${viewMode === "list" ? "active" : ""}`}
                onClick={() => setViewMode("list")}
              >
                <Flag /> Danh sách Page ({totalPagesCount})
              </button>
              <button
                type="button"
                className={`view-mode-btn ${viewMode === "grouped" ? "active" : ""}`}
                onClick={() => setViewMode("grouped")}
              >
                <UsersRound /> Nhóm theo Via ({viaGroups.length})
              </button>
            </div>

            <button
              className="secondary"
              onClick={() => setModal({ type: "job", initialAccountIds: selectedPageIds.length > 0 ? selectedPageIds : undefined })}
              title="Đăng bài cho các Fanpage"
            >
              <Layers /> Đăng hàng loạt
            </button>

            <button className="primary" onClick={() => setModal({ type: "account" })}>
              <Plus /> Thêm Fanpage
            </button>
          </div>
        }
      />

      {/* Metric Quick Stats */}
      <div className="metric-cards-row">
        <div className="metric-card">
          <div className="metric-icon" style={{ background: "#eff6ff", color: "#2563eb" }}>
            <Flag />
          </div>
          <div>
            <span>Tổng số Fanpage</span>
            <b>{totalPagesCount}</b>
          </div>
        </div>

        <div className="metric-card">
          <div className="metric-icon" style={{ background: "#ecfdf5", color: "#059669" }}>
            <MonitorDot />
          </div>
          <div>
            <span>Nick Via đang Online</span>
            <b style={{ color: "#059669" }}>{onlineVias.length}/{viaGroups.length}</b>
          </div>
        </div>

        <div className="metric-card">
          <div className="metric-icon" style={{ background: "#f5f3ff", color: "#7c3aed" }}>
            <CheckCircle2 />
          </div>
          <div>
            <span>Page Đang Hoạt động</span>
            <b style={{ color: "#7c3aed" }}>{accounts.filter((a) => a.enabled).length}</b>
          </div>
        </div>

        <div className="metric-card">
          <div className="metric-icon" style={{ background: "#fff7ed", color: "#ea580c" }}>
            <Clock3 />
          </div>
          <div>
            <span>Tác vụ đang chờ/chạy</span>
            <b style={{ color: activeQueuedJobs > 0 ? "#ea580c" : "#64748b" }}>{activeQueuedJobs}</b>
          </div>
        </div>
      </div>

      {/* Quick Filter & Search Bar */}
      <div className="filter-card">
        <div className="search-box">
          <Search />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tìm theo tên Fanpage, Page ID, tên Nick Via hoặc UID..."
          />
          {search && (
            <button className="clear-search" onClick={() => setSearch("")}>
              <X />
            </button>
          )}
        </div>

        <div className="filter-chips">
          <button
            className={`chip ${statusTab === "all" ? "active" : ""}`}
            onClick={() => setStatusTab("all")}
          >
            Tất cả ({totalPagesCount})
          </button>
          <button
            className={`chip ${statusTab === "online" ? "active" : ""}`}
            onClick={() => setStatusTab("online")}
          >
            🟢 Via Online ({masterPages.filter((p) => p.isOnline).length})
          </button>
          <button
            className={`chip ${statusTab === "enabled" ? "active" : ""}`}
            onClick={() => setStatusTab("enabled")}
          >
            Đang bật ({masterPages.filter((p) => p.enabled).length})
          </button>
          <button
            className={`chip ${statusTab === "disabled" ? "active" : ""}`}
            onClick={() => setStatusTab("disabled")}
          >
            Đã tạm dừng ({masterPages.filter((p) => !p.enabled).length})
          </button>
        </div>
      </div>

      {/* Bulk Action Floating Bar */}
      {selectedPageIds.length > 0 && (
        <div className="bulk-action-bar">
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <b>Đã chọn {selectedPageIds.length} Fanpage</b>
            <button
              type="button"
              className="text-button"
              style={{ color: "#93c5fd" }}
              onClick={() => setSelectedPageIds([])}
            >
              Bỏ chọn tất cả
            </button>
          </div>
          <button
            type="button"
            className="primary compact"
            onClick={() => setModal({ type: "job", initialAccountIds: selectedPageIds })}
          >
            <Plus /> Đăng bài cho {selectedPageIds.length} Page đã chọn
          </button>
        </div>
      )}

      {/* View Mode 1: Master Page Table List */}
      {viewMode === "list" && (
        <>
          {filteredPages.length ? (
            <div className="master-table-card">
              <table className="master-table">
                <thead>
                  <tr>
                    <th style={{ width: "40px" }}>
                      <input
                        type="checkbox"
                        checked={filteredPages.length > 0 && selectedPageIds.length === filteredPages.length}
                        onChange={toggleSelectAll}
                        title="Chọn tất cả"
                      />
                    </th>
                    <th>Fanpage</th>
                    <th>Facebook Page ID</th>
                    <th>Nick Via Quản trị</th>
                    <th>📁 Thư mục & 📜 Kịch bản gán</th>
                    <th>Trạng thái</th>
                    <th>Hàng đợi</th>
                    <th style={{ textAlign: "right" }}>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPages.map((a) => {
                    const isSelected = selectedPageIds.includes(a.id);
                    const viaName =
                      a.ext.fbUser?.name ||
                      (a.ext.id === "legacy" ? "Chrome Profile (Chưa reload)" : `Nick Via · ${a.ext.id.slice(0, 8)}`);

                    return (
                      <tr key={a.id} className={isSelected ? "selected" : ""}>
                        <td>
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => togglePageSelect(a.id)}
                          />
                        </td>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                            <div className="page-icon-badge">
                              <Flag />
                            </div>
                            <div>
                              <b style={{ fontSize: "14px", color: "#0f172a", display: "block" }}>{a.name}</b>
                              {a.notes ? (
                                <span style={{ fontSize: "11px", color: "var(--muted)" }}>🏷️ {a.notes}</span>
                              ) : (
                                <span style={{ fontSize: "11px", color: "var(--muted)" }}>Fanpage Facebook</span>
                              )}
                            </div>
                          </div>
                        </td>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                            <code
                              className="clickable-id"
                              onClick={() => copyId(a.facebook_id, "Page ID")}
                              title="Bấm để sao chép Page ID"
                            >
                              {a.facebook_id}
                            </code>
                            <a
                              href={`https://facebook.com/${a.facebook_id}`}
                              target="_blank"
                              rel="noreferrer"
                              className="icon-button"
                              title="Mở Fanpage trên Facebook"
                              style={{ width: "24px", height: "24px" }}
                            >
                              <ExternalLink style={{ width: "13px", height: "13px" }} />
                            </a>
                          </div>
                        </td>
                        <td>
                          <div>
                            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                              <span style={{ fontWeight: "700", color: "#1e293b" }}>{viaName}</span>
                              <Badge status={a.isOnline ? "online" : "offline"} />
                            </div>
                            {a.ext.fbUser?.id && (
                              <span style={{ fontSize: "11px", color: "var(--muted)" }}>
                                UID: {a.ext.fbUser.id}
                              </span>
                            )}
                          </div>
                        </td>
                        <td>
                          <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                            {a.assigned_folder ? (
                              <span style={{ fontSize: "12px", color: "#0284c7", fontWeight: "600", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                                📁 /{a.assigned_folder === "root" ? "Gốc" : a.assigned_folder}
                              </span>
                            ) : (
                              <span style={{ fontSize: "11px", color: "var(--muted)" }}>📁 Chưa gán folder</span>
                            )}
                            {a.default_script_id && scriptMap[a.default_script_id] ? (
                              <span style={{ fontSize: "11px", color: "#7c3aed", fontWeight: "500", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                                📜 {scriptMap[a.default_script_id].name}
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td>
                          <label className="read-only" style={{ cursor: "pointer" }}>
                            <input
                              type="checkbox"
                              checked={a.enabled}
                              onChange={() => toggleAccountEnabled(a)}
                            />
                            <span style={{ fontSize: "12px", fontWeight: "600", color: a.enabled ? "#059669" : "#64748b" }}>
                              {a.enabled ? "Đang bật" : "Tạm dừng"}
                            </span>
                          </label>
                        </td>
                        <td>
                          {a.activeJobs.length > 0 ? (
                            <span style={{ color: "var(--blue)", fontWeight: "700", fontSize: "11px" }}>
                              ⏳ {a.activeJobs.length} tác vụ
                            </span>
                          ) : (
                            <span style={{ color: "var(--muted)", fontSize: "11px" }}>✓ Sẵn sàng</span>
                          )}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <div style={{ display: "inline-flex", gap: "6px" }}>
                            <button
                              className="primary compact"
                              disabled={!a.enabled || !a.isOnline}
                              onClick={() => setModal({ type: "job", accountId: a.id, initialAccount: a.id })}
                              title={a.assigned_folder ? `Đăng bài từ thư mục ${a.assigned_folder}` : "Tạo bài đăng ngay cho Page này"}
                            >
                              <Plus /> Đăng ngay
                            </button>
                            <button
                              className="icon-button"
                              title="Chỉnh sửa Fanpage & Gán thư mục"
                              onClick={() => setModal({ type: "account", account: a })}
                            >
                              <Pencil />
                            </button>
                            <button
                              className="icon-button danger"
                              title="Xóa Fanpage"
                              onClick={() =>
                                setModal({
                                  type: "confirm",
                                  title: "Xóa Fanpage?",
                                  text: `Fanpage "${a.name}" sẽ bị xóa khỏi dashboard. Lịch sử job vẫn được giữ lại.`,
                                  label: "Xóa Fanpage",
                                  success: "Đã xóa Fanpage",
                                  action: () => endpoints.deleteAccount(a.id),
                                })
                              }
                            >
                              <Trash2 />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="panel">
              <Empty
                icon={Flag}
                title={search ? "Không tìm thấy Fanpage phù hợp" : "Chưa có Fanpage nào trong danh sách"}
                text={
                  search
                    ? "Thử tìm kiếm với từ khóa khác hoặc xóa bộ lọc."
                    : "Bấm 'Quét Page tự động' để hệ thống tự động tìm và nạp danh sách Fanpage từ tài khoản Facebook đang mở."
                }
                action={
                  search ? (
                    <button className="secondary" onClick={() => { setSearch(""); setStatusTab("all"); }}>
                      Xóa bộ lọc
                    </button>
                  ) : (
                    <div style={{ display: "flex", gap: "8px" }}>
                      {onlineVias.length > 0 && (
                        <button className="primary" onClick={scanAllOnlineVias} disabled={scanningAll}>
                          <Sparkles className={scanningAll ? "spin" : ""} /> Quét Page từ Nick FB
                        </button>
                      )}
                      <button className="secondary" onClick={() => setModal({ type: "account" })}>
                        <Plus /> Thêm thủ công
                      </button>
                    </div>
                  )
                }
              />
            </div>
          )}
        </>
      )}

      {/* View Mode 2: Hierarchical Groups by Via */}
      {viewMode === "grouped" && (
        <>
          {filteredGroups.length ? (
            <div className="via-groups">
              {filteredGroups.map((group) => {
                const ext = group.ext;
                const viaName =
                  ext.fbUser?.name ||
                  (ext.id === "legacy" ? "Chrome Profile (Chưa reload)" : `Nick Via · ${ext.id.slice(0, 8)}`);
                const viaUid = ext.fbUser?.id || "Chưa đồng bộ UID";
                const isOnline = group.isOnline;

                return (
                  <article className="via-card" key={ext.id}>
                    <div className="via-header">
                      <div className="via-info">
                        <div className="via-avatar">
                          <User />
                        </div>
                        <div className="via-titles">
                          <h3>
                            {viaName}
                            <Badge status={isOnline ? (ext.busy ? "running" : "online") : "offline"} />
                          </h3>
                          <p>
                            UID:{" "}
                            <b
                              className="clickable-id"
                              onClick={() => viaUid !== "Chưa đồng bộ UID" && copyId(viaUid, "UID")}
                              title="Bấm để sao chép UID"
                            >
                              {viaUid}
                            </b>{" "}
                            · Chrome Profile:{" "}
                            <code
                              className="clickable-id"
                              onClick={() => copyId(ext.id, "Profile ID")}
                              title="Bấm để sao chép Profile ID"
                            >
                              {ext.id.slice(0, 16)}...
                            </code>{" "}
                            · Đang cầm <b>{group.pages.length} Fanpage</b>
                          </p>
                        </div>
                      </div>

                      <div className="via-actions">
                        {isOnline && (
                          <>
                            <button
                              className="secondary compact"
                              onClick={() => scanAllPages(ext.id)}
                              disabled={scanningPages === ext.id}
                              title="Tự động quét toàn bộ các Fanpage do Nick này quản lý"
                            >
                              <Sparkles className={scanningPages === ext.id ? "spin" : ""} />
                              {scanningPages === ext.id ? "Đang quét..." : "Quét Page"}
                            </button>
                            {group.pages.length > 0 && (
                              <button
                                className="secondary compact"
                                onClick={() =>
                                  setModal({
                                    type: "job",
                                    initialAccountIds: group.pages.map((p) => p.id),
                                  })
                                }
                                title="Đăng bài cho toàn bộ Fanpage của Nick này"
                              >
                                <Layers /> Đăng cả nhóm ({group.pages.length})
                              </button>
                            )}
                            <button
                              className="secondary compact"
                              onClick={() => scanIdentity(ext.id)}
                              disabled={scanning === ext.id}
                              title="Quét tên và ID đang mở trên Facebook tab"
                            >
                              <RefreshCw className={scanning === ext.id ? "spin" : ""} />
                              Quét Tab
                            </button>
                          </>
                        )}
                        <button
                          className="primary compact"
                          onClick={() => setModal({ type: "account", defaultExtensionId: ext.id })}
                          title="Thêm Fanpage thủ công cho nick này"
                        >
                          <Plus /> Thêm thủ công
                        </button>
                      </div>
                    </div>

                    <div className="pages-container">
                      {group.pages.length ? (
                        <div className="pages-grid">
                          {group.pages.map((a) => {
                            const active = jobs.filter(
                              (j) =>
                                j.account_id === a.id &&
                                ["queued", "running", "waiting_connection"].includes(j.status)
                            );
                            return (
                              <div className="page-item-card" key={a.id}>
                                <div className="page-item-info">
                                  <div className="page-icon-badge">
                                    <Flag />
                                  </div>
                                  <div className="page-text">
                                    <b>{a.name}</b>
                                    <span>
                                      ID:{" "}
                                      <code
                                        className="clickable-id"
                                        onClick={() => copyId(a.facebook_id, "Page ID")}
                                        title="Bấm để sao chép Page ID"
                                      >
                                        {a.facebook_id}
                                      </code>
                                      {a.notes ? ` · ${a.notes}` : ""}
                                    </span>
                                    <small
                                      style={{
                                        color: active.length > 0 ? "var(--blue)" : "var(--muted)",
                                        fontSize: "9px",
                                        fontWeight: 700,
                                      }}
                                    >
                                      {active.length > 0 ? `⏳ ${active.length} tác vụ chờ` : "✓ Sẵn sàng"}
                                    </small>
                                  </div>
                                </div>

                                <div className="page-item-actions">
                                  <button
                                    className="icon-button"
                                    title="Chỉnh sửa Fanpage"
                                    onClick={() => setModal({ type: "account", account: a })}
                                  >
                                    <Pencil />
                                  </button>
                                  <button
                                    className="icon-button danger"
                                    title="Xóa Fanpage"
                                    onClick={() =>
                                      setModal({
                                        type: "confirm",
                                        title: "Xóa Fanpage?",
                                        text: `Fanpage "${a.name}" sẽ bị xóa khỏi dashboard. Lịch sử job vẫn được giữ lại.`,
                                        label: "Xóa Fanpage",
                                        success: "Đã xóa Fanpage",
                                        action: () => endpoints.deleteAccount(a.id),
                                      })
                                    }
                                  >
                                    <Trash2 />
                                  </button>
                                  <button
                                    className="primary compact"
                                    disabled={!a.enabled || !isOnline}
                                    onClick={() => setModal({ type: "job", accountId: a.id })}
                                    title="Tạo tác vụ đăng bài cho Page này"
                                  >
                                    <Plus /> Đăng ngay
                                  </button>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <div
                          style={{
                            textAlign: "center",
                            padding: "16px",
                            color: "var(--muted)",
                            fontSize: "12px",
                          }}
                        >
                          Nick này chưa có Fanpage nào. Bấm <b>"Quét Page"</b> để tự động lấy từ Facebook hoặc <b>"Thêm thủ công"</b>.
                        </div>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="panel">
              <Empty
                icon={UsersRound}
                title={search ? "Không tìm thấy kết quả phù hợp" : "Chưa có Nick Via nào kết nối"}
                text="Hệ thống tự động phát hiện Chrome Profile có gắn extension và tab Facebook đang mở."
              />
            </div>
          )}
        </>
      )}
    </>
  );
}

function Extensions({ extensions, accounts = [], setModal, notify, reload }) {
  return (
    <>
      <PageToolbar
        title={`${extensions.length} Chrome Profile / Nick Via`}
        subtitle="Quản lý chi tiết danh tính tài khoản Facebook, trạng thái Template và dàn Fanpage của từng Chrome Profile."
      />
      {extensions.length ? (
        <div className="extension-grid">
          {extensions.map((e) => {
            const childPages = accounts.filter((a) => a.extension_id === e.id);
            return (
              <ExtensionCard
                key={e.id}
                extension={e}
                childPages={childPages}
                setModal={setModal}
                notify={notify}
                reload={reload}
              />
            );
          })}
        </div>
      ) : (
        <div className="panel">
          <Empty
            icon={MonitorDot}
            title="Chưa có Chrome Profile nào kết nối"
            text="Hãy mở Chrome có cài đặt tiện ích FBEM và mở một tab Facebook đã đăng nhập."
          />
        </div>
      )}
    </>
  );
}

function ExtensionCard({ extension: e, childPages = [], setModal, notify, reload }) {
  const [templates, setTemplates] = useState(null);
  const [checking, setChecking] = useState(false);
  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    endpoints.templateStatus(e.id).then(setTemplates).catch(() => setTemplates(null));
  }, [e.id]);

  const identity = async () => {
    setChecking(true);
    try {
      const result = await endpoints.extensionIdentity(e.id);
      notify(`Đồng bộ thành công: ${result.name || result.id}`);
      reload();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setChecking(false);
    }
  };

  const copyId = (text, label = "ID") => {
    navigator.clipboard?.writeText(String(text));
    notify(`Đã sao chép ${label}: ${text}`);
  };

  const nickName = e.fbUser?.name || (e.id === "legacy" ? "Extension (Chưa reload)" : "Nick Facebook (Chưa đồng bộ)");
  const uid = e.fbUser?.id || null;

  return (
    <article className="extension-card">
      <div className="extension-visual">
        <User />
        <span className={`online-dot ${e.connected === false ? "offline" : ""}`} />
      </div>

      <div className="extension-title">
        <div style={{ minWidth: 0 }}>
          <h3 style={{ fontSize: "16px", fontWeight: "800", color: "#0f172a", display: "flex", alignItems: "center", gap: "8px" }}>
            {nickName}
            {e.fbUser?.name && <span className="verified-badge">✓ Nick chính</span>}
          </h3>
          <div style={{ marginTop: "4px", fontSize: "11px", color: "var(--muted)", display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <span>UID:</span>
            {uid ? (
              <code
                className="clickable-id"
                onClick={() => copyId(uid, "UID")}
                title="Bấm để sao chép UID"
              >
                {uid}
              </code>
            ) : (
              <button
                type="button"
                className="text-button"
                onClick={identity}
                disabled={checking}
                style={{ fontSize: "11px", padding: 0 }}
              >
                {checking ? "Đang đọc..." : "⚡ Bấm để đồng bộ UID"}
              </button>
            )}
            <span>· Profile:</span>
            <code
              className="clickable-id"
              onClick={() => copyId(e.id, "Profile ID")}
              title="Bấm để sao chép Profile ID"
            >
              {e.id.slice(0, 14)}...
            </code>
          </div>
        </div>
        <Badge status={e.busy ? "running" : e.connected === false ? "offline" : "online"} />
      </div>

      {/* Managed Fanpages Section */}
      <div style={{ padding: "12px 16px", background: "#f8fafc", borderRadius: "10px", margin: "14px 0", border: "1px solid #e2e8f0" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
          <span style={{ fontSize: "11px", fontWeight: "700", color: "#475569", textTransform: "uppercase", letterSpacing: "0.3px" }}>
            🚩 Đang cầm {childPages.length} Fanpage
          </span>
          <button
            type="button"
            className="text-button"
            onClick={() => setModal({ type: "account", defaultExtensionId: e.id })}
            style={{ fontSize: "11px" }}
          >
            <Plus /> Thêm Page
          </button>
        </div>

        {childPages.length > 0 ? (
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            {childPages.slice(0, 6).map((p) => (
              <span
                key={p.id}
                style={{
                  fontSize: "11px",
                  padding: "3px 8px",
                  background: "#ffffff",
                  border: "1px solid #cbd5e1",
                  borderRadius: "6px",
                  color: "#1e293b",
                  fontWeight: "600",
                }}
              >
                {p.name}
              </span>
            ))}
            {childPages.length > 6 && (
              <span style={{ fontSize: "11px", padding: "3px 8px", color: "var(--muted)" }}>
                +{childPages.length - 6} Page khác
              </span>
            )}
          </div>
        ) : (
          <div style={{ fontSize: "11px", color: "var(--muted)" }}>
            Chưa có Fanpage nào được liên kết. Bấm <b>"Quét Page từ FB"</b> để tự động lấy.
          </div>
        )}
      </div>

      <div className="metrics">
        <div>
          <span>Thành công</span>
          <b style={{ color: "#059669" }}>{e.successCount || 0}</b>
        </div>
        <div>
          <span>Thất bại</span>
          <b style={{ color: e.failedCount > 0 ? "#e11d48" : "#64748b" }}>{e.failedCount || 0}</b>
        </div>
        <div>
          <span>Đang chờ</span>
          <b>{e.pending || 0}</b>
        </div>
        <div>
          <span>Fanpage</span>
          <b style={{ color: "#2563eb" }}>{childPages.length}</b>
        </div>
      </div>

      <div className="template-status">
        <span className={templates?.reel ? "ready" : "missing"} title="Sẵn sàng đăng Reel không cần thao tác tay"><Film /> Reel Native</span>
        <span className={templates?.photo ? "ready" : "missing"} title="Sẵn sàng đăng Ảnh / Album"><Images /> Ảnh / Album</span>
        <span className={templates?.switchProfile ? "ready" : "missing"} title="Sẵn sàng chuyển quyền quản trị Fanpage"><ShieldCheck /> Chuyển Page</span>
      </div>

      <div className="extension-footer">
        <div className="last-seen">
          <Activity /> Hoạt động {relativeTime(e.lastActiveAt)}
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="secondary compact" onClick={identity} disabled={checking} title="Đọc tên và UID từ Facebook tab">
            <RefreshCw className={checking ? "spin" : ""} />
            {checking ? "Đang đọc..." : "Đồng bộ"}
          </button>
          {childPages.length > 0 && (
            <button
              className="primary compact"
              onClick={() => setModal({ type: "job", initialAccountIds: childPages.map((p) => p.id) })}
              title="Đăng bài cho dàn Fanpage của Nick này"
            >
              <Plus /> Đăng bài
            </button>
          )}
        </div>
      </div>

      {e.id === "legacy" && (
        <div className="warning-note">
          <CircleAlert /> Reload FBEM tại chrome://extensions để kích hoạt mã phiên ổn định.
        </div>
      )}
    </article>
  );
}

function MediaLibrary({ accounts = [], scripts = [], setModal, notify }) {
  const [mediaData, setMediaData] = useState({ folders: [], items: [], totalFiles: 0, totalSize: 0 });
  const [loading, setLoading] = useState(false);
  const [activeFolder, setActiveFolder] = useState("all");
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("all");

  const loadMedia = useCallback(async () => {
    setLoading(true);
    try {
      const res = await endpoints.media();
      setMediaData(res || { folders: [], items: [], totalFiles: 0, totalSize: 0 });
    } catch (e) {
      notify("Không thể tải danh sách media: " + e.message, "error");
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => {
    loadMedia();
  }, [loadMedia]);

  const linkedAccounts = useMemo(() => {
    if (activeFolder === "all") return [];
    const targetPath = activeFolder === "root" ? "root" : activeFolder;
    return accounts.filter(
      (a) => (a.assigned_folder || "") === targetPath || (!a.assigned_folder && targetPath === "root")
    );
  }, [accounts, activeFolder]);

  const filteredItems = useMemo(() => {
    let list = mediaData.items || [];
    if (activeFolder !== "all") {
      list = list.filter((m) => (activeFolder === "root" ? !m.folderPath : m.folderPath === activeFolder));
    }
    if (kindFilter !== "all") {
      list = list.filter((m) => m.kind === kindFilter);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((m) => m.name.toLowerCase().includes(q) || (m.folder && m.folder.toLowerCase().includes(q)));
    }
    return list;
  }, [mediaData.items, activeFolder, kindFilter, search]);

  const handleDeleteFolder = async (folderPath, e) => {
    e.stopPropagation();
    if (!window.confirm(`Bạn có chắc chắn muốn xóa thư mục "${folderPath}" và toàn bộ file bên trong?`)) return;
    try {
      await endpoints.deleteFolder(folderPath);
      notify(`Đã xóa thư mục ${folderPath}`);
      if (activeFolder === folderPath) setActiveFolder("all");
      loadMedia();
    } catch (err) {
      notify(err.message, "error");
    }
  };

  const handleDeleteFile = async (relPath) => {
    if (!window.confirm(`Bạn có chắc muốn xóa file "${relPath}"?`)) return;
    try {
      await endpoints.deleteMedia(relPath);
      notify(`Đã xóa file: ${relPath}`);
      loadMedia();
    } catch (err) {
      notify(err.message, "error");
    }
  };

  const copyUrl = (url, name) => {
    navigator.clipboard?.writeText(url);
    notify(`Đã sao chép đường dẫn: ${name}`);
  };

  const formatSize = (bytes) => {
    if (!bytes) return "0 MB";
    if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  };

  const formatDate = (epoch) => {
    if (!epoch) return "";
    const d = new Date(epoch * 1000);
    return `${d.toLocaleDateString("vi-VN")} ${d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}`;
  };

  return (
    <>
      <PageToolbar
        title={`${mediaData.totalFiles || 0} File Media (${formatSize(mediaData.totalSize)})`}
        subtitle="Quản lý các thư mục video và ảnh để đăng tự động lên dàn Fanpage."
        action={
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              className="secondary"
              onClick={() => setModal({ type: "createFolder", onDone: loadMedia })}
            >
              <Plus /> Tạo thư mục
            </button>
            <button
              className="primary"
              onClick={() => setModal({ type: "uploadMedia", folders: mediaData.folders, activeFolder, onDone: loadMedia })}
            >
              <Plus /> Tải video/ảnh lên
            </button>
          </div>
        }
      />

      {/* Folder selector bar */}
      <div className="folder-bar">
        <button
          type="button"
          className={`folder-chip ${activeFolder === "all" ? "active" : ""}`}
          onClick={() => setActiveFolder("all")}
        >
          <FolderTree /> Tất cả ({mediaData.totalFiles || 0})
        </button>

        <button
          type="button"
          className={`folder-chip ${activeFolder === "root" ? "active" : ""}`}
          onClick={() => setActiveFolder("root")}
        >
          <Film /> Thư mục gốc ({(mediaData.items || []).filter((m) => !m.folderPath).length})
        </button>

        {(mediaData.folders || []).map((f) => (
          <button
            key={f.path}
            type="button"
            className={`folder-chip ${activeFolder === f.path ? "active" : ""}`}
            onClick={() => setActiveFolder(f.path)}
          >
            <Film /> {f.name} ({f.count} file · {formatSize(f.size)})
            <span
              className="folder-chip-delete"
              onClick={(e) => handleDeleteFolder(f.path, e)}
              title="Xóa thư mục này"
            >
              ×
            </span>
          </button>
        ))}
      </div>

      {/* Folder link to Fanpages status banner */}
      {activeFolder !== "all" && (
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "12px",
          padding: "10px 16px",
          background: "#f0f9ff",
          border: "1px solid #bae6fd",
          borderRadius: "8px",
          marginBottom: "16px",
          flexWrap: "wrap"
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "13px", fontWeight: "700", color: "#0369a1" }}>
              📁 Thư mục: /{activeFolder === "root" ? "Gốc" : activeFolder}
            </span>
            {linkedAccounts.length > 0 ? (
              <>
                <span style={{ fontSize: "12px", color: "#0284c7" }}>· Đã gán cho <b>{linkedAccounts.length} Fanpage</b>:</span>
                {linkedAccounts.map((a) => (
                  <span
                    key={a.id}
                    style={{
                      fontSize: "11px",
                      padding: "2px 8px",
                      background: "#ffffff",
                      border: "1px solid #7dd3fc",
                      borderRadius: "12px",
                      color: "#0369a1",
                      fontWeight: "600"
                    }}
                  >
                    🚩 {a.name}
                  </span>
                ))}
              </>
            ) : (
              <span style={{ fontSize: "12px", color: "#64748b" }}>
                · Chưa có Fanpage nào gán riêng thư mục này (Vào tab <b>Quản lý Fanpage</b> $\rightarrow$ Sửa Page để gán).
              </span>
            )}
          </div>

          {linkedAccounts.length > 0 && (
            <button
              type="button"
              className="primary compact"
              onClick={() =>
                setModal({
                  type: "job",
                  initialAccountIds: linkedAccounts.map((a) => a.id),
                })
              }
              title="Đăng bài hàng loạt cho các Fanpage đã gán thư mục này"
            >
              <Layers /> Đăng lên {linkedAccounts.length} Page đã gán
            </button>
          )}
        </div>
      )}

      {/* Filter and Search Bar */}
      <div className="search-filter-bar">
        <div className="search-box">
          <Search />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tìm theo tên file video hoặc tên thư mục..."
          />
        </div>
        <div className="filter-chips">
          <button
            type="button"
            className={`filter-chip ${kindFilter === "all" ? "active" : ""}`}
            onClick={() => setKindFilter("all")}
          >
            Tất cả
          </button>
          <button
            type="button"
            className={`filter-chip ${kindFilter === "video" ? "active" : ""}`}
            onClick={() => setKindFilter("video")}
          >
            <Film /> Video Reel ({(mediaData.items || []).filter((m) => m.kind === "video").length})
          </button>
          <button
            type="button"
            className={`filter-chip ${kindFilter === "photo" ? "active" : ""}`}
            onClick={() => setKindFilter("photo")}
          >
            <Images /> Ảnh / Album ({(mediaData.items || []).filter((m) => m.kind === "photo").length})
          </button>
        </div>
      </div>

      {/* Media Grid */}
      {filteredItems.length ? (
        <div className="media-library-grid">
          {filteredItems.map((item) => (
            <article key={item.relPath} className="media-card">
              <div
                className="media-thumb-box"
                onClick={() => setModal({ type: "videoPreview", item })}
              >
                {item.kind === "video" ? (
                  <video src={item.url} preload="metadata" />
                ) : (
                  <img src={item.url} alt={item.name} />
                )}
                <div className="media-play-overlay">
                  <div className="media-play-btn">
                    {item.kind === "video" ? <Film /> : <Eye />}
                  </div>
                </div>
                <span className="media-kind-badge">
                  {item.kind === "video" ? "🎬 Video Reel" : "📸 Ảnh"}
                </span>
                {item.folder && item.folder !== "Gốc (Chưa phân loại)" && (
                  <span className="media-folder-badge">📁 {item.folder}</span>
                )}
              </div>

              <div className="media-content">
                <div className="media-filename" title={item.name}>
                  {item.name}
                </div>
                <div className="media-meta-row">
                  <span>{formatSize(item.size)}</span>
                  <span>{formatDate(item.modifiedAt)}</span>
                </div>

                <div className="media-card-actions">
                  <button
                    className="primary compact full"
                    onClick={() =>
                      setModal({
                        type: "job",
                        initialVideoUrl: item.relPath,
                        initialKind: item.kind === "video" ? "post_reel" : "post_photos",
                      })
                    }
                    title="Tạo bài đăng với video này"
                  >
                    <Plus /> Đăng bài
                  </button>
                  <button
                    className="secondary compact"
                    onClick={() => copyUrl(item.url, item.name)}
                    title="Sao chép liên kết URL nội bộ"
                  >
                    <Copy />
                  </button>
                  <button
                    className="secondary compact"
                    onClick={() => handleDeleteFile(item.relPath)}
                    title="Xóa file này"
                  >
                    <Trash2 />
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="panel">
          <Empty
            icon={Film}
            title={loading ? "Đang tải thư viện media..." : "Chưa có file nào trong thư mục này"}
            text="Hãy bấm 'Tải video/ảnh lên' hoặc 'Tạo thư mục' để bắt đầu lưu trữ video cho các chiến dịch."
            action={
              <button
                className="primary"
                onClick={() => setModal({ type: "uploadMedia", folders: mediaData.folders, activeFolder, onDone: loadMedia })}
              >
                <Plus /> Tải video lên ngay
              </button>
            }
          />
        </div>
      )}
    </>
  );
}

function Scripts({ scripts, accounts = [], setModal }) {
  return (
    <>
      <PageToolbar
        title={`${scripts.length} Kịch bản mẫu`}
        subtitle="Quản lý toàn bộ kịch bản đăng bài có sẵn và kịch bản do bạn thiết lập theo từng chủ đề."
        action={
          <div style={{ display: "flex", gap: "8px" }}>
            <button className="primary" onClick={() => setModal("script")}>
              <Plus /> Tạo kịch bản mới
            </button>
          </div>
        }
      />
      {scripts.length ? (
        <div className="script-grid">
          {scripts.map((s) => {
            const config = s.config || {};
            const linkedAccounts = accounts.filter((a) => a.default_script_id === s.id);
            const previewCaption = config.caption || "";
            const previewHashtags = config.hashtags || "";

            return (
              <article className="script-card" key={s.id} style={{ display: "flex", flexDirection: "column" }}>
                <div style={{ display: "flex", gap: "16px", alignItems: "flex-start" }}>
                  <div className={`script-symbol ${s.kind}`}><BookOpen /></div>
                  <div className="script-main" style={{ flex: 1 }}>
                    <div className="script-heading">
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <Badge status={s.enabled ? "online" : "offline"} />
                        <span style={{ fontSize: "11px", color: "var(--muted)", fontWeight: "600" }}>v{s.version}</span>
                      </div>
                      <div className="inline-actions">
                        <button
                          className="icon-button"
                          title="Chỉnh sửa kịch bản"
                          onClick={() => setModal({ type: "script", script: s })}
                        >
                          <Pencil />
                        </button>
                        <button
                          className="icon-button danger"
                          title="Xóa kịch bản"
                          onClick={() =>
                            setModal({
                              type: "confirm",
                              title: "Xóa kịch bản?",
                              text: `Kịch bản "${s.name}" sẽ bị xóa. Lịch sử các job đã chạy trước đó vẫn được giữ lại.`,
                              label: "Xóa kịch bản",
                              success: "Đã xóa kịch bản",
                              action: () => endpoints.deleteScript(s.id),
                            })
                          }
                        >
                          <Trash2 />
                        </button>
                      </div>
                    </div>
                    <h3 style={{ margin: "6px 0 4px", fontSize: "15px", fontWeight: "800", color: "#0f172a" }}>{s.name}</h3>
                    <p style={{ fontSize: "12px", color: "var(--muted)", margin: "0 0 10px", lineHeight: "1.4" }}>
                      {s.description || "Chưa có mô tả cho kịch bản này."}
                    </p>
                  </div>
                </div>

                {/* Content preview box */}
                {(previewCaption || previewHashtags) && (
                  <div style={{
                    background: "#f8fafc",
                    border: "1px solid #e2e8f0",
                    borderRadius: "10px",
                    padding: "10px 12px",
                    margin: "10px 0",
                    fontSize: "12px",
                    color: "#334155"
                  }}>
                    {previewCaption && (
                      <div style={{
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        lineHeight: "1.4"
                      }}>
                        💬 <b>Caption:</b> {previewCaption}
                      </div>
                    )}
                    {previewHashtags && (
                      <div style={{ marginTop: "6px", color: "#2563eb", fontWeight: "600", fontSize: "11px" }}>
                        🏷️ {previewHashtags}
                      </div>
                    )}
                  </div>
                )}

                {/* Linked Accounts */}
                <div style={{ margin: "4px 0 12px" }}>
                  {linkedAccounts.length > 0 ? (
                    <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
                      <span style={{ fontSize: "11px", color: "#64748b" }}>Gán cho {linkedAccounts.length} Page:</span>
                      {linkedAccounts.map((a) => (
                        <span
                          key={a.id}
                          style={{
                            fontSize: "11px",
                            padding: "2px 8px",
                            background: "#eff6ff",
                            border: "1px solid #bfdbfe",
                            borderRadius: "12px",
                            color: "#1d4ed8",
                            fontWeight: "600"
                          }}
                        >
                          🚩 {a.name}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span style={{ fontSize: "11px", color: "#94a3b8" }}>Chưa gán làm mặc định cho Page nào.</span>
                  )}
                </div>

                <div style={{ marginTop: "auto", paddingTop: "10px", borderTop: "1px solid #f1f5f9", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: "11px", color: "var(--muted)", display: "flex", alignItems: "center", gap: "4px" }}>
                    <Settings2 style={{ width: "13px", height: "13px" }} /> {KIND_LABEL[s.kind] || s.kind}
                  </span>

                  <button
                    type="button"
                    className="primary compact"
                    onClick={() =>
                      setModal({
                        type: "job",
                        initialKind: s.kind,
                        initialAccountIds: linkedAccounts.map((a) => a.id),
                      })
                    }
                    title="Mở form tạo bài đăng với kịch bản này"
                  >
                    <Plus /> Dùng kịch bản này
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="panel">
          <Empty
            icon={BookOpen}
            title="Thư viện còn trống"
            text="Tạo kịch bản để tái sử dụng caption, media và cấu hình đăng."
            action={
              <button className="primary" onClick={() => setModal("script")}>
                <Plus /> Tạo kịch bản
              </button>
            }
          />
        </div>
      )}
    </>
  );
}

function Jobs({
  jobs,
  allJobs,
  accountMap,
  query,
  setQuery,
  statusFilter,
  setStatusFilter,
  setModal,
  onCancel,
  onRetry,
}) {
  const counts = useMemo(() => {
    return {
      all: allJobs.length,
      running: allJobs.filter((j) => j.status === "running").length,
      queued: allJobs.filter((j) => ["queued", "waiting_connection"].includes(j.status)).length,
      succeeded: allJobs.filter((j) => j.status === "succeeded").length,
      failed: allJobs.filter((j) => ["failed", "cancelled"].includes(j.status)).length,
    };
  }, [allJobs]);

  return (
    <>
      <PageToolbar
        title={`${jobs.length} tác vụ`}
        subtitle="Theo dõi tiến độ chi tiết từng giai đoạn thực thi theo thời gian thực."
        action={
          <button className="primary" onClick={() => setModal({ type: "job" })}>
            <Plus /> Tạo / Chạy hàng loạt
          </button>
        }
      >
        <div className="search">
          <Search />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Tìm theo tài khoản, ID hoặc loại..."
          />
        </div>
      </PageToolbar>

      {/* Status Filter Tabs */}
      <div className="filter-tabs-bar">
        <button
          className={statusFilter === "all" ? "tab-btn active" : "tab-btn"}
          onClick={() => setStatusFilter("all")}
        >
          Tất cả <span className="tab-count">{counts.all}</span>
        </button>
        <button
          className={statusFilter === "running" ? "tab-btn active" : "tab-btn"}
          onClick={() => setStatusFilter("running")}
        >
          <span className="dot pulse-blue" /> Đang chạy <span className="tab-count">{counts.running}</span>
        </button>
        <button
          className={statusFilter === "queued" ? "tab-btn active" : "tab-btn"}
          onClick={() => setStatusFilter("queued")}
        >
          <Clock3 className="tab-icon" /> Đang chờ <span className="tab-count">{counts.queued}</span>
        </button>
        <button
          className={statusFilter === "succeeded" ? "tab-btn active" : "tab-btn"}
          onClick={() => setStatusFilter("succeeded")}
        >
          <CheckCircle2 className="tab-icon success" /> Thành công <span className="tab-count">{counts.succeeded}</span>
        </button>
        <button
          className={statusFilter === "failed" ? "tab-btn active" : "tab-btn"}
          onClick={() => setStatusFilter("failed")}
        >
          <CircleAlert className="tab-icon danger" /> Thất bại <span className="tab-count">{counts.failed}</span>
        </button>
      </div>

      <div className="panel table-panel">
        {jobs.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Tác vụ</th>
                  <th>Tài khoản / Page</th>
                  <th>Tiến độ xử lý</th>
                  <th>Thời lượng & Lần thử</th>
                  <th>Trạng thái</th>
                  <th>Kết quả / Phản hồi</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => {
                  const prog = computeJobProgress(j);
                  const duration = formatDuration(j.started_at, j.finished_at, j.status);
                  return (
                    <tr key={j.id} className={j.status === "running" ? "row-running" : ""}>
                      <td>
                        <div className="job-kind">
                          <div><Bot /></div>
                          <span>
                            <b>{KIND_LABEL[j.kind] || j.kind}</b>
                            <small>{j.id.slice(0, 8)}</small>
                          </span>
                        </div>
                      </td>
                      <td>
                        <b>{accountMap[j.account_id]?.name || "Không xác định"}</b>
                        <small className="cell-subtext">ID: {accountMap[j.account_id]?.facebook_id || "—"}</small>
                      </td>
                      <td className="progress-cell">
                        <div className="table-progress-wrap">
                          <div className="table-progress-labels">
                            <span className="table-stage-text">{prog.stage}</span>
                            <span className="table-percent">{prog.percent}%</span>
                          </div>
                          <div className="progress-bar-wrap mini">
                            <div
                              className={`progress-bar-fill ${prog.tone}`}
                              style={{ width: `${prog.percent}%` }}
                            />
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className="duration-tag"><Timer /> {duration}</span>
                        <small>{j.attempts}/{j.max_attempts} lần thử</small>
                      </td>
                      <td>
                        <Badge status={j.status} />
                      </td>
                      <td className={j.error ? "error-text" : "result-text"}>
                        {j.error || (j.result ? "Đã có kết quả xuất bản" : "—")}
                      </td>
                      <td>
                        <div className="row-actions">
                          <button
                            className="icon-button"
                            title="Xem tiến độ & chi tiết"
                            onClick={() => setModal({ type: "job-detail", job: j })}
                          >
                            <Eye />
                          </button>
                          {["failed", "cancelled"].includes(j.status) && (
                            <button
                              className="icon-button"
                              title="Chạy lại tác vụ"
                              onClick={() => onRetry(j.id)}
                            >
                              <RotateCcw />
                            </button>
                          )}
                          {["queued", "waiting_connection"].includes(j.status) && (
                            <button
                              className="icon-button danger"
                              title="Hủy tác vụ"
                              onClick={() => onCancel(j.id)}
                            >
                              <X />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={History}
            title="Không có tác vụ phù hợp"
            text="Tạo tác vụ mới hoặc chuyển đổi bộ lọc trạng thái."
          />
        )}
      </div>
    </>
  );
}

function PageToolbar({ title, subtitle, action, children }) {
  return (
    <div className="page-toolbar">
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
      <div className="toolbar-actions">
        {children}
        {action}
      </div>
    </div>
  );
}

function AccountModal({ extensions, account, scripts = [], defaultExtensionId, close, submit }) {
  const [form, setForm] = useState({
    name: account?.name || "",
    facebookId: account?.facebook_id || "",
    extensionId: account?.extension_id || defaultExtensionId || extensions[0]?.id || "",
    accountType: account?.account_type || "page",
    notes: account?.notes || "",
    assignedFolder: account?.assigned_folder || "",
    defaultScriptId: account?.default_script_id || "",
    enabled: account?.enabled ?? true,
  });

  const [folders, setFolders] = useState([]);
  const [detecting, setDetecting] = useState(false);

  useEffect(() => {
    endpoints.media().then((res) => {
      if (res?.folders) setFolders(res.folders);
    }).catch(() => {});
  }, []);

  const detectFromTab = async () => {
    if (!form.extensionId) return;
    setDetecting(true);
    try {
      const res = await endpoints.extensionIdentity(form.extensionId);
      if (res && res.id) {
        setForm((prev) => ({
          ...prev,
          name: prev.name || res.name || "Fanpage Facebook",
          facebookId: res.id,
        }));
      }
    } catch (err) {
      console.warn("Could not auto-detect identity:", err);
    } finally {
      setDetecting(false);
    }
  };

  return (
    <Modal
      title={account ? "Chỉnh sửa Fanpage / Tài khoản" : "Thêm Fanpage do Nick Via quản lý"}
      subtitle="Định tuyến Chrome Profile, gán Thư mục Video và Kịch bản mặc định."
      onClose={close}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(form);
        }}
      >
        <Field label="Nick Via / Extension quản lý (Chrome Profile)">
          <select
            required
            value={form.extensionId}
            onChange={(e) => setForm({ ...form, extensionId: e.target.value })}
          >
            <option value="">Chọn Chrome Profile / Nick Via</option>
            {!extensions.some((x) => x.id === form.extensionId) && form.extensionId && (
              <option value={form.extensionId}>{form.extensionId} (offline)</option>
            )}
            {extensions.map((x) => (
              <option key={x.id} value={x.id}>
                {x.fbUser?.name ? `${x.fbUser.name} (${x.id.slice(0, 8)})` : x.id}
              </option>
            ))}
          </select>
          <small>Chọn nick Facebook (trình duyệt) có quyền quản trị Fanpage này.</small>
        </Field>

        <Field label="Tên Fanpage">
          <input
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Ví dụ: Hội Những Người Yêu Da Đẹp"
          />
        </Field>

        <Field label="Facebook / Page ID">
          <div style={{ display: "flex", gap: "8px" }}>
            <input
              required
              value={form.facebookId}
              onChange={(e) =>
                setForm({ ...form, facebookId: e.target.value.replace(/\D/g, "") })
              }
              placeholder="61550123456789"
              style={{ flex: 1 }}
            />
            {form.extensionId && (
              <button
                type="button"
                className="secondary compact"
                onClick={detectFromTab}
                disabled={detecting}
                title="Lấy ID từ tab Facebook đang mở"
              >
                <Sparkles className={detecting ? "spin" : ""} />
                Lấy từ Tab FB
              </button>
            )}
          </div>
          <small>ID của Fanpage (hoặc UID cá nhân nếu muốn đăng lên tường cá nhân).</small>
        </Field>

        <Field label="📁 Thư mục Video/Media gán riêng cho Page này">
          <select
            value={form.assignedFolder}
            onChange={(e) => setForm({ ...form, assignedFolder: e.target.value })}
          >
            <option value="">-- Chưa gán (Chọn từ toàn bộ thư viện) --</option>
            <option value="root">📁 Gốc (Chưa phân loại)</option>
            {folders.map((f) => (
              <option key={f.path} value={f.path}>
                📁 {f.name} ({f.count} file · {f.path})
              </option>
            ))}
          </select>
          <small>Khi tạo bài đăng cho Page này, hệ thống sẽ ưu tiên gợi ý video từ thư mục này.</small>
        </Field>

        <Field label="📜 Kịch bản mẫu mặc định">
          <select
            value={form.defaultScriptId}
            onChange={(e) => setForm({ ...form, defaultScriptId: e.target.value })}
          >
            <option value="">-- Chưa gán kịch bản mặc định --</option>
            {scripts.filter((s) => s.enabled).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({KIND_LABEL[s.kind] || s.kind})
              </option>
            ))}
          </select>
          <small>Kịch bản sẽ tự động chọn sẵn khi bạn tạo bài đăng mới cho Fanpage này.</small>
        </Field>

        <Field label="Ghi chú phân loại">
          <input
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
            placeholder="Ví dụ: Page bán hàng, Page tin tức vệ tinh..."
          />
        </Field>

        <label className="toggle-row">
          <div>
            <b>Cho phép chạy tác vụ</b>
            <span>Tắt để tạm dừng Fanpage mà không xóa dữ liệu.</span>
          </div>
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          />
        </label>
        <ModalActions
          close={close}
          disabled={!form.extensionId || !form.name.trim() || !form.facebookId.trim()}
          label={account ? "Cập nhật Fanpage" : "Thêm Fanpage"}
        />
      </form>
    </Modal>
  );
}

const POPULAR_HASHTAGS = ["#reels", "#xuhuong", "#viral", "#trending", "#facebookreels", "#shortvideo", "#fbem", "#fyp"];

function HashtagPicker({ value, onChange }) {
  const addTag = (tag) => {
    const current = (value || "").trim();
    if (!current) {
      onChange(tag);
    } else if (!current.includes(tag)) {
      onChange(`${current} ${tag}`);
    }
  };

  return (
    <div className="hashtag-chips">
      {POPULAR_HASHTAGS.map((tag) => (
        <button
          key={tag}
          type="button"
          className="hashtag-chip"
          onClick={() => addTag(tag)}
        >
          {tag}
        </button>
      ))}
    </div>
  );
}

function MediaPicker({ kind = "video", selected, onSelect, initialFolder }) {
  const [mediaData, setMediaData] = useState({ folders: [], items: [] });
  const [loading, setLoading] = useState(false);
  const [folderFilter, setFolderFilter] = useState(initialFolder || "all");

  useEffect(() => {
    if (initialFolder !== undefined && initialFolder !== "") {
      setFolderFilter(initialFolder);
    }
  }, [initialFolder]);

  const fetchMedia = useCallback(async () => {
    setLoading(true);
    try {
      const res = await endpoints.media();
      setMediaData(res || { folders: [], items: [] });
    } catch {
      setMediaData({ folders: [], items: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMedia();
  }, [fetchMedia]);

  const filtered = useMemo(() => {
    let list = mediaData.items || [];
    if (kind) list = list.filter((m) => m.kind === kind);
    if (folderFilter !== "all") {
      list = list.filter((m) => (folderFilter === "root" ? !m.folderPath : m.folderPath === folderFilter));
    }
    return list;
  }, [mediaData.items, kind, folderFilter]);

  return (
    <div>
      <div style={{ display: "flex", gap: "8px", marginBottom: "8px" }}>
        <select
          value={folderFilter}
          onChange={(e) => setFolderFilter(e.target.value)}
          style={{ width: "200px", fontSize: "12px" }}
        >
          <option value="all">📁 Tất cả thư mục ({mediaData.items?.length || 0})</option>
          <option value="root">📁 Gốc (Chưa phân loại)</option>
          {(mediaData.folders || []).map((f) => (
            <option key={f.path} value={f.path}>
              📁 {f.name} ({f.count})
            </option>
          ))}
        </select>

        <div className="media-select-row" style={{ flex: 1, marginBottom: 0 }}>
          <select
            value={selected || ""}
            onChange={(e) => onSelect(e.target.value)}
          >
            <option value="">-- Chọn file ({filtered.length} file) --</option>
            {filtered.map((m) => (
              <option key={m.relPath} value={m.relPath}>
                {m.name} ({(m.size / (1024 * 1024)).toFixed(1)} MB) {m.folderPath ? `[📁 ${m.folderPath}]` : ""}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="icon-button"
            onClick={fetchMedia}
            title="Làm mới danh sách media"
          >
            <RefreshCw className={loading ? "spin" : ""} />
          </button>
        </div>
      </div>

      {filtered.length > 0 && (
        <div className="media-chip-list">
          {filtered.slice(0, 6).map((m) => (
            <button
              key={m.relPath}
              type="button"
              className={`media-chip ${selected === m.relPath ? "active" : ""}`}
              onClick={() => onSelect(m.relPath)}
            >
              <Film /> {m.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ScriptModal({ script, close, submit }) {
  const initialConfig = script?.config || {};
  const [mode, setMode] = useState("visual");
  const [form, setForm] = useState({
    name: script?.name || "",
    description: script?.description || "",
    kind: script?.kind || "post_reel",
    enabled: script?.enabled ?? true,
    videoUrl: initialConfig.videoUrl || "",
    imageUrls: Array.isArray(initialConfig.imageUrls)
      ? initialConfig.imageUrls.join("\n")
      : initialConfig.imageUrls || "",
    caption: initialConfig.caption || "Nội dung cho {{account_name}} ngày {{date}}",
    hashtags: initialConfig.hashtags || "#reels #xuhuong",
    pageId: initialConfig.pageId || "",
    configText: script
      ? JSON.stringify(script.config || {}, null, 2)
      : '{\n  "caption": "Nội dung cho {{account_name}} ngày {{date}}"\n}',
  });
  const [jsonError, setJsonError] = useState("");

  const send = (e) => {
    e.preventDefault();
    if (mode === "json") {
      try {
        const config = JSON.parse(form.configText);
        setJsonError("");
        submit({
          name: form.name,
          description: form.description,
          kind: form.kind,
          config,
          enabled: form.enabled,
        });
      } catch {
        setJsonError("JSON không hợp lệ. Vui lòng kiểm tra dấu ngoặc và dấu phẩy.");
      }
    } else {
      let fullCaption = form.caption.trim();
      if (form.hashtags.trim()) {
        fullCaption = fullCaption ? `${fullCaption}\n\n${form.hashtags.trim()}` : form.hashtags.trim();
      }

      let config = {};
      if (form.kind === "post_reel") {
        config = {
          videoUrl: form.videoUrl.trim(),
          caption: fullCaption,
        };
      } else if (form.kind === "post_photos") {
        const urls = form.imageUrls
          .split("\n")
          .map((u) => u.trim())
          .filter(Boolean);
        config = {
          imageUrls: urls,
          caption: fullCaption,
        };
      } else if (form.kind === "switch_profile") {
        config = {
          targetId: form.pageId.trim(),
        };
      }

      if (form.pageId.trim() && form.kind !== "switch_profile") {
        config.pageId = form.pageId.trim();
      }

      submit({
        name: form.name,
        description: form.description,
        kind: form.kind,
        config,
        enabled: form.enabled,
      });
    }
  };

  return (
    <Modal
      title={script ? "Chỉnh sửa kịch bản" : "Tạo kịch bản mới"}
      subtitle="Kịch bản tái sử dụng cấu hình và hỗ trợ biến động {{account_name}}, {{date}}."
      onClose={close}
    >
      <form onSubmit={send}>
        <Field label="Tên kịch bản">
          <input
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Đăng Reel buổi sáng"
          />
        </Field>
        <div className="form-grid">
          <Field label="Loại kịch bản">
            <select
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value })}
            >
              {Object.entries(KIND_LABEL)
                .filter(([k]) => k !== "get_identity")
                .map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Trạng thái">
            <label className="read-only">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              {form.enabled ? "Đang sử dụng" : "Đã tạm dừng"}
            </label>
          </Field>
        </div>
        <Field label="Mô tả">
          <textarea
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder="Mục đích và cách dùng kịch bản..."
          />
        </Field>

        <div className="segmented">
          <button
            type="button"
            className={mode === "visual" ? "active" : ""}
            onClick={() => setMode("visual")}
          >
            Form Trực quan (Dễ dùng)
          </button>
          <button
            type="button"
            className={mode === "json" ? "active" : ""}
            onClick={() => {
              let fullCaption = form.caption.trim();
              if (form.hashtags.trim()) {
                fullCaption = fullCaption ? `${fullCaption}\n\n${form.hashtags.trim()}` : form.hashtags.trim();
              }
              const cfg =
                form.kind === "post_reel"
                  ? { videoUrl: form.videoUrl, caption: fullCaption, ...(form.pageId ? { pageId: form.pageId } : {}) }
                  : form.kind === "post_photos"
                  ? { imageUrls: form.imageUrls.split("\n").filter(Boolean), caption: fullCaption }
                  : { targetId: form.pageId };
              setForm((f) => ({ ...f, configText: JSON.stringify(cfg, null, 2) }));
              setMode("json");
            }}
          >
            Cấu hình JSON nâng cao
          </button>
        </div>

        {mode === "visual" ? (
          <>
            {form.kind === "post_reel" && (
              <Field label="File Video (.mp4 / Chọn từ media hoặc nhập đường dẫn)">
                <input
                  required
                  value={form.videoUrl}
                  onChange={(e) => setForm({ ...form, videoUrl: e.target.value })}
                  placeholder="Ví dụ: clip1.mp4 hoặc C:/Videos/clip1.mp4"
                />
                <MediaPicker
                  kind="video"
                  selected={form.videoUrl}
                  onSelect={(file) => setForm({ ...form, videoUrl: file })}
                />
              </Field>
            )}

            {form.kind === "post_photos" && (
              <Field label="Danh sách file ảnh (mỗi file 1 dòng)">
                <textarea
                  required
                  value={form.imageUrls}
                  onChange={(e) => setForm({ ...form, imageUrls: e.target.value })}
                  placeholder="photo1.jpg&#10;photo2.png"
                  rows={3}
                />
                <MediaPicker
                  kind="photo"
                  onSelect={(file) =>
                    setForm((f) => ({
                      ...f,
                      imageUrls: f.imageUrls ? `${f.imageUrls}\n${file}` : file,
                    }))
                  }
                />
              </Field>
            )}

            {form.kind !== "switch_profile" && (
              <>
                <Field label="Nội dung Caption bài đăng">
                  <textarea
                    required
                    value={form.caption}
                    onChange={(e) => setForm({ ...form, caption: e.target.value })}
                    placeholder="Nhập nội dung bài đăng..."
                    rows={3}
                  />
                  <small>Biến hỗ trợ: {"{{account_name}}"}, {"{{facebook_id}}"}, {"{{date}}"}</small>
                </Field>

                <Field label="Hashtags (Gắn thẻ xu hướng)">
                  <input
                    value={form.hashtags}
                    onChange={(e) => setForm({ ...form, hashtags: e.target.value })}
                    placeholder="Ví dụ: #reels #xuhuong #fbem"
                  />
                  <HashtagPicker
                    value={form.hashtags}
                    onChange={(tags) => setForm({ ...form, hashtags: tags })}
                  />
                </Field>

                <Field label="ID Fanpage đăng bài (Tùy chọn)">
                  <input
                    value={form.pageId}
                    onChange={(e) => setForm({ ...form, pageId: e.target.value })}
                    placeholder="Để trống nếu đăng bằng chính tài khoản hiện tại"
                  />
                </Field>
              </>
            )}

            {form.kind === "switch_profile" && (
              <Field label="ID Fanpage / Profile cần chuyển sang">
                <input
                  required
                  value={form.pageId}
                  onChange={(e) => setForm({ ...form, pageId: e.target.value })}
                  placeholder="Nhập Facebook ID của Page mục tiêu"
                />
              </Field>
            )}
          </>
        ) : (
          <Field label="Cấu hình JSON">
            <textarea
              className="code-input"
              value={form.configText}
              onChange={(e) => setForm({ ...form, configText: e.target.value })}
            />
            {jsonError && <small className="field-error">{jsonError}</small>}
            <small>Biến hỗ trợ: {"{{account_name}}"}, {"{{facebook_id}}"}, {"{{date}}"}</small>
          </Field>
        )}

        <ModalActions close={close} label={script ? "Lưu phiên bản mới" : "Tạo kịch bản"} />
      </form>
    </Modal>
  );
}

function JobModal({ accounts, scripts, initialAccount, initialAccountIds, initialVideoUrl, initialKind, close, submit }) {
  const enabled = accounts.filter((a) => a.enabled);
  const defaultAccountIds =
    initialAccountIds && initialAccountIds.length > 0
      ? initialAccountIds
      : initialAccount
      ? [initialAccount]
      : enabled[0]
      ? [enabled[0].id]
      : [];

  const firstAcc = accounts.find((a) => defaultAccountIds.includes(a.id));
  const initScriptId = firstAcc?.default_script_id || scripts.find((s) => s.enabled)?.id || "";
  const initMode = initialVideoUrl ? "direct" : firstAcc?.default_script_id ? "script" : scripts.some((s) => s.enabled) ? "script" : "direct";

  const [form, setForm] = useState({
    accountIds: defaultAccountIds,
    mode: initMode,
    scriptId: initScriptId,
    kind: initialKind || "post_reel",
    videoUrl: initialVideoUrl || "",
    caption: "",
    hashtags: "#reels #xuhuong",
    pageId: "",
    inputText: "{}",
    useJsonOverride: false,
    scheduled: "",
  });
  const [jsonError, setJsonError] = useState("");

  const selectedAccounts = useMemo(
    () => accounts.filter((a) => form.accountIds.includes(a.id)),
    [accounts, form.accountIds]
  );
  const primaryAccount = selectedAccounts[0];
  const assignedFolder = primaryAccount?.assigned_folder;
  const defaultScript = scripts.find((s) => s.id === primaryAccount?.default_script_id);

  const toggle = (id) =>
    setForm((current) => ({
      ...current,
      accountIds: current.accountIds.includes(id)
        ? current.accountIds.filter((x) => x !== id)
        : [...current.accountIds, id],
    }));

  const send = (e) => {
    e.preventDefault();
    try {
      let input = {};
      if (form.useJsonOverride) {
        input = JSON.parse(form.inputText);
      } else if (form.mode === "direct") {
        let fullCaption = form.caption.trim();
        if (form.hashtags.trim()) {
          fullCaption = fullCaption ? `${fullCaption}\n\n${form.hashtags.trim()}` : form.hashtags.trim();
        }
        if (form.kind === "post_reel") {
          input = { videoUrl: form.videoUrl.trim(), caption: fullCaption };
        } else if (form.kind === "post_photos") {
          input = { imageUrls: [form.videoUrl.trim()], caption: fullCaption };
        } else if (form.kind === "switch_profile") {
          input = { targetId: form.pageId.trim() };
        }
        if (form.pageId.trim() && form.kind !== "switch_profile") {
          input.pageId = form.pageId.trim();
        }
      }

      if (form.scheduled) {
        input.scheduledPublishTime = Math.floor(new Date(form.scheduled).getTime() / 1000);
      }

      setJsonError("");
      submit({
        accountIds: form.accountIds,
        scriptId: form.mode === "script" ? form.scriptId : undefined,
        kind: form.mode === "direct" ? form.kind : undefined,
        input,
      });
    } catch {
      setJsonError("JSON ghi đè hoặc thời gian lên lịch không hợp lệ.");
    }
  };

  return (
    <Modal
      title="Tạo tác vụ mới"
      subtitle="Chọn một hoặc nhiều tài khoản; hệ thống tự tách hàng đợi và thực thi an toàn."
      onClose={close}
    >
      <form onSubmit={send}>
        <Field label="Fanpage / Tài khoản thực thi">
          <div className="account-picker">
            <div className="picker-head">
              <span>Đã chọn {form.accountIds.length}/{enabled.length} Fanpage</span>
              <button
                type="button"
                onClick={() =>
                  setForm({
                    ...form,
                    accountIds:
                      form.accountIds.length === enabled.length
                        ? []
                        : enabled.map((a) => a.id),
                  })
                }
              >
                {form.accountIds.length === enabled.length ? "Bỏ chọn" : "Chọn tất cả"}
              </button>
            </div>
            {Object.entries(
              enabled.reduce((acc, a) => {
                const k = a.extension_id || "Khác";
                if (!acc[k]) acc[k] = [];
                acc[k].push(a);
                return acc;
              }, {})
            ).map(([extId, pageList]) => {
              const allSelected = pageList.every((p) => form.accountIds.includes(p.id));
              return (
                <div key={extId} className="picker-via-group">
                  <div className="picker-via-title">
                    <span>👤 Nick Via · {extId.slice(0, 14)}... ({pageList.length} Page)</span>
                    <button
                      type="button"
                      onClick={() => {
                        const ids = pageList.map((p) => p.id);
                        if (allSelected) {
                          setForm({
                            ...form,
                            accountIds: form.accountIds.filter((id) => !ids.includes(id)),
                          });
                        } else {
                          setForm({
                            ...form,
                            accountIds: Array.from(new Set([...form.accountIds, ...ids])),
                          });
                        }
                      }}
                    >
                      {allSelected ? "Bỏ chọn nhóm" : "Chọn cả nhóm"}
                    </button>
                  </div>
                  {pageList.map((a) => (
                    <label key={a.id}>
                      <input
                        type="checkbox"
                        checked={form.accountIds.includes(a.id)}
                        onChange={() => toggle(a.id)}
                      />
                      <span>
                        <b>{a.name}</b>
                        <small>
                          ID: {a.facebook_id} {a.assigned_folder ? `· 📁 /${a.assigned_folder}` : ""} {a.notes ? `· ${a.notes}` : ""}
                        </small>
                      </span>
                    </label>
                  ))}
                </div>
              );
            })}
          </div>

          {primaryAccount && (primaryAccount.assigned_folder || primaryAccount.default_script_id) && (
            <div style={{
              background: "#f0fdf4",
              border: "1px solid #bbf7d0",
              borderRadius: "8px",
              padding: "8px 12px",
              marginTop: "8px",
              display: "flex",
              flexDirection: "column",
              gap: "4px",
              fontSize: "12px",
              color: "#166534"
            }}>
              <div style={{ fontWeight: "700" }}>⚙️ Thiết lập đã gán cho Fanpage "{primaryAccount.name}":</div>
              {primaryAccount.assigned_folder && (
                <div>📁 Thư mục video: <b>/{primaryAccount.assigned_folder === "root" ? "Gốc" : primaryAccount.assigned_folder}</b> (Hệ thống đã tự động chọn thư mục này bên dưới)</div>
              )}
              {defaultScript && (
                <div>📜 Kịch bản mẫu mặc định: <b>{defaultScript.name}</b></div>
              )}
            </div>
          )}
        </Field>

        <div className="segmented">
          <button
            type="button"
            className={form.mode === "script" ? "active" : ""}
            onClick={() => setForm({ ...form, mode: "script" })}
          >
            Từ kịch bản mẫu
          </button>
          <button
            type="button"
            className={form.mode === "direct" ? "active" : ""}
            onClick={() => setForm({ ...form, mode: "direct" })}
          >
            Tác vụ trực tiếp
          </button>
        </div>

        {form.mode === "script" ? (
          <Field label="Kịch bản">
            <select
              required
              value={form.scriptId}
              onChange={(e) => setForm({ ...form, scriptId: e.target.value })}
            >
              <option value="">Chọn kịch bản</option>
              {scripts
                .filter((s) => s.enabled)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · {KIND_LABEL[s.kind]}
                  </option>
                ))}
            </select>
          </Field>
        ) : (
          <>
            <Field label="Loại tác vụ">
              <select
                value={form.kind}
                onChange={(e) => setForm({ ...form, kind: e.target.value })}
              >
                {Object.entries(KIND_LABEL).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>

            {form.kind === "post_reel" && (
              <Field label="File Video (.mp4 / media)">
                <input
                  required={!form.useJsonOverride}
                  value={form.videoUrl}
                  onChange={(e) => setForm({ ...form, videoUrl: e.target.value })}
                  placeholder="Ví dụ: clip1.mp4 hoặc C:/Videos/clip1.mp4"
                />
                <MediaPicker
                  kind="video"
                  initialFolder={assignedFolder}
                  selected={form.videoUrl}
                  onSelect={(file) => setForm({ ...form, videoUrl: file })}
                />
              </Field>
            )}

            {form.kind === "post_photos" && (
              <Field label="File Ảnh (.jpg, .png / media)">
                <input
                  required={!form.useJsonOverride}
                  value={form.videoUrl}
                  onChange={(e) => setForm({ ...form, videoUrl: e.target.value })}
                  placeholder="Ví dụ: photo1.jpg hoặc C:/Images/photo1.jpg"
                />
                <MediaPicker
                  kind="photo"
                  initialFolder={assignedFolder}
                  selected={form.videoUrl}
                  onSelect={(file) => setForm({ ...form, videoUrl: file })}
                />
              </Field>
            )}

            {form.kind !== "switch_profile" && form.kind !== "get_identity" && (
              <>
                <Field label="Nội dung Caption">
                  <textarea
                    required={!form.useJsonOverride}
                    value={form.caption}
                    onChange={(e) => setForm({ ...form, caption: e.target.value })}
                    placeholder="Nhập caption bài đăng..."
                    rows={3}
                  />
                </Field>

                <Field label="Hashtags">
                  <input
                    value={form.hashtags}
                    onChange={(e) => setForm({ ...form, hashtags: e.target.value })}
                    placeholder="Ví dụ: #reels #xuhuong #fbem"
                  />
                  <HashtagPicker
                    value={form.hashtags}
                    onChange={(tags) => setForm({ ...form, hashtags: tags })}
                  />
                </Field>
              </>
            )}
          </>
        )}

        <Field label="Thời gian Facebook xuất bản (Hẹn giờ đăng)">
          <input
            type="datetime-local"
            value={form.scheduled}
            onChange={(e) => setForm({ ...form, scheduled: e.target.value })}
          />
          <small>Chọn ngày giờ để Facebook tự động lên lịch xuất bản đúng giờ đó.</small>
        </Field>

        <div className="toggle-row">
          <div>
            <b>Cấu hình JSON nâng cao</b>
            <span>Bật nếu bạn muốn tự viết JSON ghi đè payload</span>
          </div>
          <input
            type="checkbox"
            checked={form.useJsonOverride}
            onChange={(e) => setForm({ ...form, useJsonOverride: e.target.checked })}
          />
        </div>

        {form.useJsonOverride && (
          <Field label="Input / Ghi đè cấu hình JSON">
            <textarea
              className="code-input"
              value={form.inputText}
              onChange={(e) => setForm({ ...form, inputText: e.target.value })}
            />
            {jsonError && <small className="field-error">{jsonError}</small>}
            <small>Reel: videoUrl, caption · Ảnh: imageUrls, caption · Switch: targetId</small>
          </Field>
        )}

        {!enabled.length && (
          <div className="warning-note">
            <CircleAlert /> Cần có ít nhất một tài khoản đang bật.
          </div>
        )}

        <ModalActions
          close={close}
          disabled={!form.accountIds.length || (form.mode === "script" && !form.scriptId)}
          label={form.accountIds.length > 1 ? `Tạo ${form.accountIds.length} tác vụ` : "Đưa vào hàng đợi"}
        />
      </form>
    </Modal>
  );
}

function JobDetail({ job, account, close, retry }) {
  const copy = (value) => navigator.clipboard?.writeText(JSON.stringify(value, null, 2));
  const prog = computeJobProgress(job);
  const steps = getPipelineSteps(job);
  const duration = formatDuration(job.started_at, job.finished_at, job.status);

  return (
    <Modal title="Chi tiết tiến độ tác vụ" subtitle={`ID Tác vụ: ${job.id}`} onClose={close}>
      {/* Progress Bar & Status */}
      <div className="detail-progress-section">
        <div className="detail-progress-head">
          <div>
            <span className="stage-title">{prog.stage}</span>
            <Badge status={job.status} />
          </div>
          <strong className="stage-percent">{prog.percent}%</strong>
        </div>
        <div className="progress-bar-wrap large">
          <div
            className={`progress-bar-fill ${prog.tone}`}
            style={{ width: `${prog.percent}%` }}
          />
        </div>
      </div>

      {/* Visual Pipeline Stepper */}
      <div className="stepper-wrap">
        <h3>Quy trình xử lý tuần tự (Execution Pipeline)</h3>
        <div className="pipeline-stepper">
          {steps.map((step, idx) => (
            <div className={`stepper-step ${step.status}`} key={idx}>
              <div className="step-marker">
                {step.status === "completed" ? (
                  <CheckCircle2 />
                ) : step.status === "active" ? (
                  <RefreshCw className="spin" />
                ) : step.status === "failed" ? (
                  <CircleAlert />
                ) : (
                  <span>{idx + 1}</span>
                )}
              </div>
              <div className="step-info">
                <b>{step.title}</b>
                <small>{step.desc}</small>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Metrics & Timers Grid */}
      <div className="detail-grid">
        <div>
          <span>Tài khoản / Fanpage</span>
          <b>{account?.name || job.account_id}</b>
        </div>
        <div>
          <span>Loại tác vụ</span>
          <b>{KIND_LABEL[job.kind] || job.kind}</b>
        </div>
        <div>
          <span>Thời lượng thực thi</span>
          <b><Timer /> {duration}</b>
        </div>
        <div>
          <span>Số lần thử (Attempts)</span>
          <b>{job.attempts}/{job.max_attempts} lần</b>
        </div>
      </div>

      <div className="timeline-info-grid">
        <div>
          <span>Tạo lúc</span>
          <small>{formatFullTime(job.created_at)}</small>
        </div>
        <div>
          <span>Bắt đầu lúc</span>
          <small>{formatFullTime(job.started_at)}</small>
        </div>
        <div>
          <span>Hoàn tất lúc</span>
          <small>{formatFullTime(job.finished_at)}</small>
        </div>
      </div>

      {job.error && (
        <div className="detail-error">
          <CircleAlert />
          <div>
            <b>Lỗi thực thi:</b>
            <p>{job.error}</p>
          </div>
        </div>
      )}

      <JsonBlock title="Input cấu hình đã mở rộng" value={job.input} copy={copy} />
      <JsonBlock title="Kết quả phản hồi từ Facebook" value={job.result} copy={copy} />

      <div className="modal-actions">
        <button className="secondary" onClick={close}>Đóng</button>
        {["failed", "cancelled"].includes(job.status) && (
          <button className="primary" onClick={retry}>
            <RotateCcw /> Chạy lại ngay
          </button>
        )}
      </div>
    </Modal>
  );
}

function JsonBlock({ title, value, copy }) {
  return (
    <div className="json-block">
      <div>
        <b>{title}</b>
        <button onClick={() => copy(value)}><Copy /> Sao chép</button>
      </div>
      <pre>{JSON.stringify(value ?? null, null, 2)}</pre>
    </div>
  );
}

function ConfirmModal({ title, text, label, close, confirm }) {
  return (
    <Modal title={title} subtitle="Hành động này cần được xác nhận." onClose={close}>
      <div className="confirm-content">
        <div><Trash2 /></div>
        <p>{text}</p>
      </div>
      <div className="modal-actions">
        <button className="secondary" onClick={close}>Quay lại</button>
        <button className="danger-button" onClick={confirm}>{label}</button>
      </div>
    </Modal>
  );
}

function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function ModalActions({ close, disabled, label = "Lưu thay đổi" }) {
  return (
    <div className="modal-actions">
      <button type="button" className="secondary" onClick={close}>Hủy</button>
      <button className="primary" disabled={disabled}>{label}</button>
    </div>
  );
}

function CreateFolderModal({ close, onDone, notify }) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    try {
      await endpoints.createFolder(name.trim());
      notify(`Đã tạo thư mục: ${name.trim()}`);
      close();
      onDone?.();
    } catch (err) {
      notify(err.message || "Không thể tạo thư mục", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Tạo thư mục video mới" subtitle="Phân loại video theo chủ đề hoặc từng dàn Fanpage" onClose={close}>
      <form onSubmit={handleCreate}>
        <Field label="Tên thư mục">
          <input
            required
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Ví dụ: Video_Kem_Body, Tin_Tuc_Mien_Tay, Reels_Giai_Tri..."
          />
        </Field>
        <ModalActions close={close} disabled={!name.trim() || saving} label={saving ? "Đang tạo..." : "Tạo thư mục"} />
      </form>
    </Modal>
  );
}

function UploadMediaModal({ folders = [], activeFolder, close, onDone, notify }) {
  const [selectedFolder, setSelectedFolder] = useState(activeFolder !== "all" && activeFolder !== "root" ? activeFolder : "");
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);

  const handleUpload = async (e) => {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("folder", selectedFolder);
      await endpoints.uploadMedia(formData);
      notify(`Đã tải lên thành công: ${file.name}`);
      close();
      onDone?.();
    } catch (err) {
      notify(err.message || "Lỗi khi tải lên file", "error");
    } finally {
      setUploading(false);
    }
  };

  return (
    <Modal title="Tải Video hoặc Ảnh lên thư viện" subtitle="File sẽ được lưu trữ cục bộ trên máy chủ" onClose={close}>
      <form onSubmit={handleUpload}>
        <Field label="Lưu vào thư mục">
          <select value={selectedFolder} onChange={(e) => setSelectedFolder(e.target.value)}>
            <option value="">-- Thư mục gốc (Chưa phân loại) --</option>
            {folders.map((f) => (
              <option key={f.path} value={f.path}>
                📁 {f.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Chọn file video / ảnh từ máy tính (.mp4, .mov, .jpg, .png)">
          <div className="dropzone-box" onClick={() => document.getElementById("file-upload-input")?.click()}>
            <div className="dropzone-icon">
              <Film />
            </div>
            <b>{file ? file.name : "Nhấp để chọn file hoặc kéo thả vào đây"}</b>
            <p style={{ fontSize: "11px", color: "var(--muted)", marginTop: "4px" }}>
              {file ? `Dung lượng: ${(file.size / (1024 * 1024)).toFixed(2)} MB` : "Hỗ trợ định dạng video MP4, MOV, MKV và ảnh JPEG, PNG"}
            </p>
            <input
              id="file-upload-input"
              type="file"
              accept="video/*,image/*"
              style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && setFile(e.target.files[0])}
            />
          </div>
        </Field>

        <ModalActions close={close} disabled={!file || uploading} label={uploading ? "Đang tải lên..." : "Tải lên thư viện"} />
      </form>
    </Modal>
  );
}

function VideoPreviewModal({ item, close, setModal }) {
  if (!item) return null;
  return (
    <Modal title={`Xem trước: ${item.name}`} subtitle={`Dung lượng: ${(item.size / (1024 * 1024)).toFixed(1)} MB · Thư mục: ${item.folder || "Gốc"}`} onClose={close}>
      <div style={{ textAlign: "center" }}>
        {item.kind === "video" ? (
          <video src={item.url} controls autoPlay className="video-modal-player" />
        ) : (
          <img src={item.url} alt={item.name} style={{ maxWidth: "100%", maxHeight: "450px", borderRadius: "12px", objectFit: "contain", marginBottom: "16px" }} />
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "10px" }}>
        <button type="button" className="secondary" onClick={close}>
          Đóng
        </button>
        <button
          type="button"
          className="primary"
          onClick={() => {
            close();
            setModal({
              type: "job",
              initialVideoUrl: item.relPath,
              initialKind: item.kind === "video" ? "post_reel" : "post_photos",
            });
          }}
        >
          <Plus /> Đăng ngay bài này
        </button>
      </div>
    </Modal>
  );
}

