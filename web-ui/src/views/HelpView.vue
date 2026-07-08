<script setup lang="ts">
import { t } from "@/i18n";
</script>

<template>
  <section class="content-page">
    <header class="page-header">
      <div>
        <div class="eyebrow">Help</div>
        <h1 v-text="t('help.title')"></h1>
        <p class="page-subtitle" v-text="t('help.subtitle')"></p>
      </div>
    </header>

    <Card>
      <template #content>
        <div class="help-body">
          <h3>1. Chat &amp; trích dẫn</h3>
          <p>
            Đặt câu hỏi ở trang Chat. Câu trả lời kèm trích dẫn tài liệu; bấm vào trích dẫn để mở đúng trang PDF gốc.
            Bạn có thể gửi kèm ảnh để hỏi theo hình.
          </p>
          <h3>2. Tải &amp; ingest tài liệu</h3>
          <p>
            Vào <strong>Tải tài liệu</strong> để tải file (PDF, DOCX, XLSX…), chọn phòng ban và mức mật. Hệ thống tạo
            job ingest; theo dõi tiến độ ở <strong>Tiến trình ingest</strong> (huỷ, xếp lại, ưu tiên, xoá).
          </p>
          <h3>3. Duyệt &amp; xuất bản</h3>
          <p>
            Reviewer/Admin vào <strong>Duyệt tài liệu</strong> để xuất bản phiên bản mới / variant / độc lập, từ chối hoặc
            lưu trữ.
          </p>
          <h3>4. Phân quyền</h3>
          <p>
            Người dùng gửi <strong>Yêu cầu quyền</strong> (nâng mức mật / thêm phòng ban). Admin quản lý tài khoản, vai
            trò, phòng ban, site ở trang <strong>Người dùng</strong>.
          </p>
          <h3>5. Chất lượng &amp; theo dõi</h3>
          <p>
            Các trang Feedback, Regression, Chất lượng, Báo cáo sử dụng, Observability và Audit Log giúp giám sát và
            cải thiện hệ thống.
          </p>
          <h3>6. Ngôn ngữ</h3>
          <p>Chuyển Việt/Anh bằng nút ngôn ngữ trên thanh điều hướng; lựa chọn được lưu theo tài khoản.</p>
        </div>
      </template>
    </Card>
  </section>
</template>
