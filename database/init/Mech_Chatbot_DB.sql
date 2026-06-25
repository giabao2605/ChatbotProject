SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO
-- ============================================================
-- SCRIPT KHOI TAO DATABASE - AN TOAN CHO PRODUCTION
-- File: database/init/Mech_Chatbot_DB.sql
-- 
-- MUC DICH: Tao database va cac bang NEU CHUA TON TAI.
-- Script nay TUYET DOI KHONG xoa du lieu hien co.
-- Co the chay lai nhieu lan ma khong gay mat du lieu.
--
-- CANH BAO: Neu ban can xoa/reset toan bo du lieu de phat trien,
--           hay dung script: scripts/danger_ops/reset_and_create_dev_db.sql
--           TUYET DOI KHONG dung script do tren moi truong Production.
-- ============================================================

-- Buoc 1: Tao database neu chua ton tai
IF NOT EXISTS (
    SELECT 1
    FROM sys.databases
    WHERE name = 'Mech_Chatbot_DB'
)
BEGIN
    CREATE DATABASE Mech_Chatbot_DB;
END
GO

USE Mech_Chatbot_DB;
GO

-- ==========================================
-- PHAN 1: QUAN LY TAI LIEU (Documents) & QUEUE
-- ==========================================

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.IngestionJobs') AND type = 'U')
BEGIN
    CREATE TABLE IngestionJobs (
        JobID                    INT IDENTITY(1, 1) PRIMARY KEY,
        TenFile                  NVARCHAR(255) NOT NULL,
        FilePath                 NVARCHAR(500) NOT NULL,
        ThuMuc                   NVARCHAR(255),
        Status                   NVARCHAR(50) DEFAULT 'pending',
        -- Cac gia tri hop le: pending, classifying, extracting, embedding,
        -- pending_review, pending_retry, waiting_quota, publishing, published, failed, rejected
        ErrorMessage             NVARCHAR(MAX),
        UploadedBy               NVARCHAR(255) NULL,
        RequestedAction          NVARCHAR(50)  NULL,
        ClassificationJson       NVARCHAR(MAX) NULL,
        ClassificationConfidence FLOAT         NULL,
        RetryCount               INT DEFAULT 0,
        MaxRetry                 INT DEFAULT 3,
        LockedBy                 NVARCHAR(255) NULL,
        LockedAt                 DATETIME      NULL,
        ProgressPercent          INT DEFAULT 0,
        -- Phase 7: quality reporting
        FailureType              NVARCHAR(50)  NULL,  -- e.g. 'gemini_quota'
        NextRetryAt              DATETIME      NULL,  -- dung cho trang thai waiting_quota
        ExtractionReport         NVARCHAR(MAX) NULL,  -- JSON bao cao chat luong trich xuat
        QualityScore             INT           NULL,  -- 0-100
        QualityStatus            NVARCHAR(50)  NULL,  -- e.g. 'good', 'low_quality', 'failed'
        CreatedAt                DATETIME DEFAULT GETDATE(),
        UpdatedAt                DATETIME DEFAULT GETDATE()
    );
END
GO

-- Index ho tro get_pending_job (WHERE Status, NextRetryAt)
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_IngestionJobs_Status_NextRetryAt' AND object_id = OBJECT_ID('dbo.IngestionJobs'))
BEGIN
    CREATE INDEX IX_IngestionJobs_Status_NextRetryAt ON IngestionJobs(Status, NextRetryAt);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.DocumentFamily') AND type = 'U')
BEGIN
    CREATE TABLE DocumentFamily (
        FamilyID    INT IDENTITY(1,1) PRIMARY KEY,
        BaseCode    NVARCHAR(255) NOT NULL,
        FamilyName  NVARCHAR(500),
        Department  NVARCHAR(255),
        Description NVARCHAR(MAX),
        CreatedAt   DATETIME DEFAULT GETDATE(),
        UpdatedAt   DATETIME DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_DocumentFamily_BaseCode' AND object_id = OBJECT_ID('dbo.DocumentFamily'))
BEGIN
    CREATE UNIQUE INDEX UX_DocumentFamily_BaseCode ON DocumentFamily(BaseCode);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.TaiLieu') AND type = 'U')
BEGIN
    CREATE TABLE TaiLieu (
        DocID           INT IDENTITY(1, 1) PRIMARY KEY,
        TenFile         NVARCHAR(255) NOT NULL,
        ThuMuc          NVARCHAR(255),
        NgayTaiLen      DATETIME DEFAULT GETDATE(),
        TrangThaiVector BIT DEFAULT 0,
        TrangThai       NVARCHAR(50) DEFAULT 'published', -- Legacy field
        NgayDuyet       DATETIME,
        NguoiDuyet      NVARCHAR(255),
        LyDoTuChoi      NVARCHAR(MAX),
        -- Versioning & lifecycle
        FamilyID        INT NULL,
        BaseCode        NVARCHAR(255) NULL,
        VersionNo       INT NULL,
        VersionLabel    NVARCHAR(50)  NULL,
        VariantCode     NVARCHAR(255) DEFAULT 'default',
        VariantGroup    NVARCHAR(255) NULL,
        LifecycleStatus NVARCHAR(50)  DEFAULT 'draft',
        ReviewStatus    NVARCHAR(50)  DEFAULT 'pending_review',
        IsCurrent       BIT DEFAULT 0,
        IsArchived      BIT DEFAULT 0,
        SupersedesDocID INT NULL,
        PublishedAt     DATETIME NULL,
        ArchivedAt      DATETIME NULL,
        UploadedBy      NVARCHAR(255) NULL,
        ReviewedBy      NVARCHAR(255) NULL,
        ClassificationConfidence FLOAT        NULL,
        ClassificationJson       NVARCHAR(MAX) NULL,
        -- Multi-domain (P0)
        Domain          NVARCHAR(50)  NULL,               -- co_khi / ky_thuat / ke_toan / nhan_su / chung
        SecurityLevel   NVARCHAR(20)  NOT NULL DEFAULT 'internal',  -- public / internal / confidential
        Site            NVARCHAR(100) NULL,               -- khu/xuong/site (P1)
        CONSTRAINT FK_TaiLieu_Family
            FOREIGN KEY (FamilyID) REFERENCES DocumentFamily(FamilyID),
        CONSTRAINT CHK_LifecycleStatus
            CHECK (LifecycleStatus IN ('draft', 'published', 'archived', 'superseded', 'retired', 'rejected', 'deleting')),
        CONSTRAINT CHK_ReviewStatus
            CHECK (ReviewStatus IN ('pending_review', 'approved', 'rejected'))
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_BaseCode_Current' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_BaseCode_Current ON TaiLieu(BaseCode, IsCurrent, LifecycleStatus, ReviewStatus);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_BaseCode_Version' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_BaseCode_Version ON TaiLieu(BaseCode, VersionNo, VariantCode);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_Family_Variant_Current' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_Family_Variant_Current ON TaiLieu(FamilyID, VariantCode, IsCurrent);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_TaiLieu_Current_Per_Variant' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE UNIQUE INDEX UX_TaiLieu_Current_Per_Variant ON TaiLieu(BaseCode, VariantCode)
        WHERE IsCurrent = 1 AND LifecycleStatus = 'published';
GO

-- ==========================================
-- PHAN 2: DU LIEU KY THUAT CO KHI
-- ==========================================

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.TaiLieuKyThuat') AND type = 'U')
BEGIN
    CREATE TABLE TaiLieuKyThuat (
        ID              INT IDENTITY(1, 1) PRIMARY KEY,
        DocID           INT,
        TrangSo         INT,
        LoaiTaiLieu     NVARCHAR(255),
        MaDoiTuong      NVARCHAR(MAX),   -- Danh sach ma doi tuong dang JSON string
        TenSanPham      NVARCHAR(500),   -- Ten san pham / Tieu de
        CongDoan        NVARCHAR(255),   -- To san xuat / Quy trinh
        VatLieu         NVARCHAR(255),   -- Vat lieu
        SoLuong         INT,             -- So luong
        NguoiLap        NVARCHAR(255),   -- Noi rong tranh truncate
        NgayVe          DATE,            -- Ngay phat hanh / Ngay ve
        DungSaiDay      NVARCHAR(255),
        DungSaiKhac     NVARCHAR(255),
        KichThuocTongThe NVARCHAR(255),
        HDCV            NVARCHAR(MAX),   -- Huong dan cong viec
        YCKT            NVARCHAR(MAX),   -- Yeu cau ky thuat
        CONSTRAINT FK_TaiLieuKyThuat_TaiLieu
            FOREIGN KEY (DocID) REFERENCES TaiLieu(DocID) ON DELETE CASCADE,
        CONSTRAINT UQ_TaiLieuKyThuat_Doc_Trang
            UNIQUE (DocID, TrangSo)
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.BangKeVatTu') AND type = 'U')
BEGIN
    CREATE TABLE BangKeVatTu (
        ID                 INT IDENTITY(1, 1) PRIMARY KEY,
        DocID              INT NOT NULL,
        TrangSo            INT,
        MaHang             NVARCHAR(255),
        TenVatTu           NVARCHAR(500),
        VatLieu            NVARCHAR(255),
        NormalizedMaterial NVARCHAR(255) NULL,
        SoLuong            INT,
        GhiChu             NVARCHAR(MAX),
        Unit               NVARCHAR(50)  NULL,
        Confidence         FLOAT         NULL,
        RawRowJson         NVARCHAR(MAX) NULL,
        SourceTableIndex   INT           NULL,
        CONSTRAINT FK_BangKeVatTu_TaiLieu
            FOREIGN KEY (DocID) REFERENCES TaiLieu(DocID) ON DELETE CASCADE
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BangKeVatTu_DocID' AND object_id = OBJECT_ID('dbo.BangKeVatTu'))
    CREATE INDEX IX_BangKeVatTu_DocID ON BangKeVatTu(DocID);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BangKeVatTu_MaHang' AND object_id = OBJECT_ID('dbo.BangKeVatTu'))
    CREATE INDEX IX_BangKeVatTu_MaHang ON BangKeVatTu(MaHang);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BangKeVatTu_VatLieu' AND object_id = OBJECT_ID('dbo.BangKeVatTu'))
    CREATE INDEX IX_BangKeVatTu_VatLieu ON BangKeVatTu(VatLieu);
GO

-- Full-Text Index cho search_bom_by_code (thay the LIKE %...%)
-- Yeu cau: SQL Server Full-Text Search service phai duoc cai dat.
-- Kiem tra: SELECT FULLTEXTSERVICEPROPERTY('IsFullTextInstalled') -- phai tra ve 1
IF FULLTEXTSERVICEPROPERTY('IsFullTextInstalled') = 1
BEGIN
    IF NOT EXISTS (SELECT 1 FROM sys.fulltext_catalogs WHERE name = 'FT_MechChatbot')
    BEGIN
        CREATE FULLTEXT CATALOG FT_MechChatbot AS DEFAULT;
    END
END
GO

IF FULLTEXTSERVICEPROPERTY('IsFullTextInstalled') = 1
AND NOT EXISTS (
    SELECT 1 FROM sys.fulltext_indexes fi
    JOIN sys.objects o ON fi.object_id = o.object_id
    WHERE o.name = 'BangKeVatTu'
)
BEGIN
    -- Lay ten PK dong tu sys.key_constraints thay vi hard-code ten tu sinh
    -- (SQL Server tu sinh hau to ngau nhien nhu PK__BangKeVa__3214EC07 nen khac nhau moi DB)
    DECLARE @pk_name NVARCHAR(256);
    SELECT @pk_name = kc.name
    FROM sys.key_constraints kc
    JOIN sys.objects o ON kc.parent_object_id = o.object_id
    WHERE o.name = 'BangKeVatTu' AND kc.type = 'PK';

    IF @pk_name IS NOT NULL
    BEGIN
        DECLARE @sql NVARCHAR(MAX);
        SET @sql = N'
            CREATE FULLTEXT INDEX ON BangKeVatTu(
                MaHang   LANGUAGE 1033,
                TenVatTu LANGUAGE 1066
            )
            KEY INDEX ' + QUOTENAME(@pk_name) + N'
            ON FT_MechChatbot
            WITH CHANGE_TRACKING AUTO;';
        EXEC sp_executesql @sql;
    END
END
GO

-- ==========================================
-- PHAN 2B: TRANG TAI LIEU & THUOC TINH KY THUAT (Phase 7)
-- ==========================================
-- Bang DocumentPages: luu tung trang PDF sau khi trich xuat
-- Dung boi: save_document_page(), delete_document_completely()

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.DocumentPages') AND type = 'U')
BEGIN
    CREATE TABLE DocumentPages (
        PageID             INT IDENTITY(1,1) PRIMARY KEY,
        DocID              INT NOT NULL,
        FileName           NVARCHAR(500) NOT NULL,
        PageNo             INT NOT NULL,
        TextExtract        NVARCHAR(MAX)  NULL,   -- Van ban trich xuat tho tu PDF
        LocalOCRText       NVARCHAR(MAX)  NULL,   -- Van ban tu OCR local
        VisionSummary      NVARCHAR(MAX)  NULL,   -- Mo ta tu Vision LLM
        LocalOCRConfidence FLOAT          NULL,   -- Do tin cay OCR (0.0 - 1.0)
        ExtractionStatus   NVARCHAR(50)   NULL,   -- e.g. 'ok', 'ocr_only', 'failed'
        ImagePath          NVARCHAR(1000) NULL,   -- Duong dan anh PNG cua trang
        CreatedAt          DATETIME DEFAULT GETDATE(),
        UpdatedAt          DATETIME DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DocumentPages_DocID_PageNo' AND object_id = OBJECT_ID('dbo.DocumentPages'))
BEGIN
    CREATE INDEX IX_DocumentPages_DocID_PageNo ON DocumentPages(DocID, PageNo);
END
GO

-- Bang TechnicalAttributes: luu cac thuoc tinh ky thuat chi tiet trich xuat tu Vision LLM
-- Dung boi: save_technical_attributes(), get_technical_attributes_for_rag(),
--           verify_technical_attribute(), delete_document_completely()

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.TechnicalAttributes') AND type = 'U')
BEGIN
    CREATE TABLE TechnicalAttributes (
        AttributeID    INT IDENTITY(1,1) PRIMARY KEY,
        DocID          INT NOT NULL,
        FileName       NVARCHAR(500) NOT NULL,
        PageNo         INT           NULL,
        AttributeType  NVARCHAR(100) NOT NULL,   -- e.g. 'tolerance', 'material', 'dimension'
        AttributeName  NVARCHAR(255) NULL,        -- Ten thuoc tinh cu the
        AttributeValue NVARCHAR(500) NOT NULL,   -- Gia tri thuoc tinh
        Unit           NVARCHAR(50)  NULL,        -- Don vi do luong
        SourceText     NVARCHAR(MAX) NULL,        -- Doan van ban goc lam can cu
        Confidence     FLOAT         NULL,        -- Do tin cay trich xuat (0.0 - 1.0)
        ExtractedBy    NVARCHAR(50)  NULL,        -- e.g. 'vision_llm', 'ocr'
        HumanVerified  BIT DEFAULT 0,             -- Da duoc con nguoi xac nhan chua
        VerifiedBy     NVARCHAR(255) NULL,
        VerifiedAt     DATETIME      NULL,
        CreatedAt      DATETIME DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TechnicalAttributes_File_Type' AND object_id = OBJECT_ID('dbo.TechnicalAttributes'))
BEGIN
    CREATE INDEX IX_TechnicalAttributes_File_Type ON TechnicalAttributes(FileName, AttributeType);
END
GO

-- ==========================================
-- PHAN 3: LUU TRU LICH SU CHAT
-- ==========================================

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.LichSuChat') AND type = 'U')
BEGIN
    CREATE TABLE LichSuChat (
        ChatID        INT IDENTITY(1, 1) PRIMARY KEY,
        SessionID     VARCHAR(100)  NOT NULL,
        CauHoi_User   NVARCHAR(MAX) NOT NULL,
        TraLoi_Bot    NVARCHAR(MAX) NOT NULL,
        HinhAnhUpload NVARCHAR(500),             -- Duong dan anh user upload (neu co)
        RefImages     NVARCHAR(MAX),             -- Danh sach duong dan ban ve can cu dang JSON
        DanhGia       TINYINT,                  -- 1: Like, -1: Dislike, NULL: Chua danh gia
        ThoiGian      DATETIME DEFAULT GETDATE(),
        Username      NVARCHAR(255) NOT NULL     -- Bat buoc: phan tach lich su chat theo user
    );
END
GO

-- Fallback: DB cu chua co cot Username -> them vao (NULL cho row cu la chap nhan duoc)
IF COL_LENGTH('dbo.LichSuChat', 'Username') IS NULL
BEGIN
    ALTER TABLE dbo.LichSuChat ADD Username NVARCHAR(255) NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.FeedbackReview') AND type = 'U')
BEGIN
    CREATE TABLE FeedbackReview (
        FeedbackID        INT IDENTITY(1, 1) PRIMARY KEY,
        ChatID            INT NOT NULL,
        Question          NVARCHAR(MAX),
        BotAnswer         NVARCHAR(MAX),
        FailureType       NVARCHAR(100),
        CorrectAnswer     NVARCHAR(MAX),
        CorrectSourceDocID INT NULL,
        ReviewerNote      NVARCHAR(MAX),
        AddedToGoldenSet  BIT DEFAULT 0,
        CreatedAt         DATETIME DEFAULT GETDATE(),
        CONSTRAINT FK_FeedbackReview_ChatID
            FOREIGN KEY (ChatID) REFERENCES LichSuChat(ChatID) ON DELETE CASCADE
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_LichSuChat_Session_Time' AND object_id = OBJECT_ID('dbo.LichSuChat'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_LichSuChat_Session_Time ON LichSuChat(SessionID, ThoiGian);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_LichSuChat_Username_Time' AND object_id = OBJECT_ID('dbo.LichSuChat'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_LichSuChat_Username_Time ON LichSuChat(Username, ThoiGian DESC);
END
GO

-- ==========================================
-- PHAN 4: AUTH & ROLES
-- ==========================================

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.Users') AND type = 'U')
BEGIN
    CREATE TABLE Users (
        UserID       INT IDENTITY(1,1) PRIMARY KEY,
        Username     NVARCHAR(255) UNIQUE NOT NULL,
        PasswordHash NVARCHAR(500) NOT NULL,
        DisplayName  NVARCHAR(255),
        Department   NVARCHAR(255),
        IsActive     BIT DEFAULT 1,
        CreatedAt    DATETIME DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.Roles') AND type = 'U')
BEGIN
    CREATE TABLE Roles (
        RoleID   INT IDENTITY(1,1) PRIMARY KEY,
        RoleName NVARCHAR(100) UNIQUE NOT NULL
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.UserRoles') AND type = 'U')
BEGIN
    CREATE TABLE UserRoles (
        UserID INT,
        RoleID INT,
        PRIMARY KEY (UserID, RoleID)
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.UserDepartments') AND type = 'U')
BEGIN
    CREATE TABLE UserDepartments (
        UserID     INT NOT NULL,
        Department NVARCHAR(255) NOT NULL,
        PRIMARY KEY (UserID, Department)
    );
END
GO

-- Muc mat cho user (RBAC 2 chieu: department x security_level)
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.UserSecurityClearance') AND type = 'U')
BEGIN
    CREATE TABLE UserSecurityClearance (
        UserID   INT NOT NULL PRIMARY KEY,
        MaxLevel NVARCHAR(20) NOT NULL DEFAULT 'internal',  -- public / internal / confidential
        CONSTRAINT FK_USC_Users FOREIGN KEY (UserID) REFERENCES Users(UserID)
    );
END
GO

-- Seed Roles neu bang trong (idempotent)
IF NOT EXISTS (SELECT 1 FROM dbo.Roles)
BEGIN
    SET IDENTITY_INSERT dbo.Roles OFF;
    INSERT INTO Roles (RoleName) VALUES ('admin'), ('reviewer'), ('uploader'), ('viewer');
END
GO

-- Seed nguoi dung admin mac dinh neu chua co
-- Mat khau mac dinh: Admin@123 (bcrypt hash)
IF NOT EXISTS (SELECT 1 FROM dbo.Users WHERE Username = 'admin')
BEGIN
    INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
    VALUES ('admin', '$2b$12$GjF79FWNuuNfl4VWOA28iOk4ubZWWd5OltSsAiZ5TgaWPz5UtAZpu', 'Administrator', 'IT');

    INSERT INTO UserRoles (UserID, RoleID)
    SELECT u.UserID, r.RoleID
    FROM Users u, Roles r
    WHERE u.Username = 'admin' AND r.RoleName = 'admin';

    INSERT INTO UserDepartments (UserID, Department)
    SELECT UserID, 'IT' FROM Users WHERE Username = 'admin';

    -- Admin: muc mat cao nhat
    INSERT INTO UserSecurityClearance (UserID, MaxLevel)
    SELECT UserID, 'confidential' FROM Users WHERE Username = 'admin';
END
GO

-- Seed nguoi dung test (chi tao neu chua co)
IF NOT EXISTS (SELECT 1 FROM dbo.Users WHERE Username = 'viewer1')
BEGIN
    INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
    VALUES ('viewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Nhan Vien A', 'Tu_Hoc');

    INSERT INTO UserRoles (UserID, RoleID)
    SELECT u.UserID, r.RoleID
    FROM Users u, Roles r
    WHERE u.Username = 'viewer1' AND r.RoleName = 'viewer';

    -- viewer1 chi xem tai lieu Tu_Hoc va CHUNG (khong phai "Tu_Hoc" la dept label)
    INSERT INTO UserDepartments (UserID, Department)
    SELECT u.UserID, d.Department
    FROM Users u
    CROSS JOIN (VALUES ('Tu_Hoc'), ('CHUNG')) AS d(Department)
    WHERE u.Username = 'viewer1';

    -- viewer1: chi xem duoc tai lieu internal va public (khong xem confidential)
    INSERT INTO UserSecurityClearance (UserID, MaxLevel)
    SELECT UserID, 'internal' FROM Users WHERE Username = 'viewer1';
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.Users WHERE Username = 'uploader1')
BEGIN
    INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
    VALUES ('uploader1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Uploader', 'Ky_Thuat');

    INSERT INTO UserRoles (UserID, RoleID)
    SELECT u.UserID, r.RoleID
    FROM Users u, Roles r
    WHERE u.Username = 'uploader1' AND r.RoleName = 'uploader';

    -- uploader1: duoc nap len tai lieu cho cac to san xuat
    INSERT INTO UserDepartments (UserID, Department)
    SELECT u.UserID, d.Department
    FROM Users u
    CROSS JOIN (VALUES
        ('To_Han'), ('To_Dap'), ('To_Son'), ('To_Nham'),
        ('To_Phoi'), ('To_Tien_Phay'), ('To_Dong_Goi'),
        ('To_Ban_Le'), ('Bang_Ke'), ('Gia_Cong_Ngoai'), ('CHUNG')
    ) AS d(Department)
    WHERE u.Username = 'uploader1';

    -- uploader1: internal level
    INSERT INTO UserSecurityClearance (UserID, MaxLevel)
    SELECT UserID, 'internal' FROM Users WHERE Username = 'uploader1';
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.Users WHERE Username = 'reviewer1')
BEGIN
    INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
    VALUES ('reviewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Truong Phong', 'Ky_Thuat');

    INSERT INTO UserRoles (UserID, RoleID)
    SELECT u.UserID, r.RoleID
    FROM Users u, Roles r
    WHERE u.Username = 'reviewer1' AND r.RoleName = 'reviewer';

    -- reviewer1: duyet tai lieu cua tat ca cac to (doc toan quyen)
    INSERT INTO UserDepartments (UserID, Department)
    SELECT u.UserID, d.Department
    FROM Users u
    CROSS JOIN (VALUES
        ('To_Han'), ('To_Dap'), ('To_Son'), ('To_Nham'),
        ('To_Phoi'), ('To_Tien_Phay'), ('To_Dong_Goi'),
        ('To_Ban_Le'), ('Bang_Ke'), ('Gia_Cong_Ngoai'), ('IT'), ('Tu_Hoc'), ('CHUNG')
    ) AS d(Department)
    WHERE u.Username = 'reviewer1';

    -- reviewer1: xem duoc ca confidential
    INSERT INTO UserSecurityClearance (UserID, MaxLevel)
    SELECT UserID, 'confidential' FROM Users WHERE Username = 'reviewer1';
END
GO

-- ==========================================
-- PHAN 5: AUDIT LOG
-- ==========================================

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.AuditLog') AND type = 'U')
BEGIN
    CREATE TABLE AuditLog (
        AuditID    INT IDENTITY(1,1) PRIMARY KEY,
        UserID     INT NULL,
        Username   NVARCHAR(255),
        Action     NVARCHAR(100) NOT NULL,
        EntityType NVARCHAR(100),
        EntityID   INT NULL,
        Details    NVARCHAR(MAX),
        CreatedAt  DATETIME DEFAULT GETDATE()
    );
END
GO

-- ==========================================
-- PHAN 5B: METADATA TONG QUAT (da domain, P0)
-- ==========================================
-- Bang DocumentAttributes: luu metadata cho domain phi co khi (ke_toan, nhan_su...)
-- Vi du: so_hop_dong, ky_luong, phong_ban, v.v.

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.DocumentAttributes') AND type = 'U')
BEGIN
    CREATE TABLE DocumentAttributes (
        AttrID         INT IDENTITY(1,1) PRIMARY KEY,
        DocID          INT NOT NULL,
        Domain         NVARCHAR(50)  NOT NULL,
        AttributeKey   NVARCHAR(150) NOT NULL,
        AttributeValue NVARCHAR(MAX) NULL,
        Confidence     FLOAT NULL,
        ExtractedBy    NVARCHAR(50) NULL,        -- 'regex' | 'llm' | 'manual'
        CreatedAt      DATETIME DEFAULT GETDATE(),
        CONSTRAINT FK_DocAttr_TaiLieu FOREIGN KEY (DocID) REFERENCES TaiLieu(DocID) ON DELETE CASCADE
    );
    CREATE INDEX IX_DocAttr_Doc_Domain ON DocumentAttributes(DocID, Domain);
END
GO

-- ==========================================
-- KIEM TRA KET QUA
-- ==========================================
SELECT
    t.TABLE_NAME,
    COUNT(c.COLUMN_NAME) AS SoCot
FROM INFORMATION_SCHEMA.TABLES t
JOIN INFORMATION_SCHEMA.COLUMNS c ON t.TABLE_NAME = c.TABLE_NAME
WHERE t.TABLE_TYPE = 'BASE TABLE'
GROUP BY t.TABLE_NAME
ORDER BY t.TABLE_NAME;

SELECT DB_NAME() AS [Database hien tai], GETDATE() AS [Thoi gian chay];
GO