SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO
-- ============================================================================
-- BASELINE SCHEMA (v2) — nguon su that DUY NHAT cho cau truc DB
-- File: database/schema/01_baseline.sql
--
-- MUC TIEU CUA BAN LAM SACH NAY:
--  1) GOM tat ca dinh nghia bang vao 1 file (truoc day bi trung giua
--     init/*.sql va migrations/migrate_p0/p3_*). Day la nguon su that duy nhat.
--  2) MOI COT MOI = mot khoi ALTER idempotent RIENG (KHONG nhet cot moi vao
--     trong CREATE TABLE bi bao boi IF NOT EXISTS(bang) -> tranh loi 'DB cu
--     khong nhan cot' nhu Domain/SecurityLevel/Site truoc day).
--  3) Theo doi phien ban bang dbo._SchemaVersions (thay cho ghi chu "chay sau").
--  4) Thong nhat tien to dbo. + 1 khoa phong ban duy nhat (Departments.DeptCode).
--
-- AN TOAN: idempotent — chay lai nhieu lan KHONG mat du lieu.
-- Thu tu chay (xem database/MIGRATIONS.md):
--   1. database/schema/01_baseline.sql        (file nay)
--   2. database/seed/*.sql                    (roles, tai khoan dev, 14 phong ban)
--   3. database/migrations/V0001__*.sql ...    (cac thay doi sau nay, danh so tang dan)
--   4. database/data_migrations/*.sql          (don du lieu cu khi can)
-- ============================================================================

-- Buoc 0: Tao database neu chua ton tai
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = 'Mech_Chatbot_DB')
BEGIN
    CREATE DATABASE Mech_Chatbot_DB;
END
GO

USE Mech_Chatbot_DB;
GO

-- ==========================================================================
-- PHAN 0: THEO DOI PHIEN BAN SCHEMA
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo._SchemaVersions') AND type = 'U')
BEGIN
    CREATE TABLE dbo._SchemaVersions (
        Version     NVARCHAR(50)  NOT NULL PRIMARY KEY,  -- vd: 'baseline_v2', 'V0001'
        Description NVARCHAR(255) NULL,
        AppliedAt   DATETIME      NOT NULL DEFAULT GETDATE()
    );
END
GO

-- ==========================================================================
-- PHAN 1: QUAN LY TAI LIEU (Documents) & QUEUE
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.IngestionJobs') AND type = 'U')
BEGIN
    CREATE TABLE dbo.IngestionJobs (
        JobID                    INT IDENTITY(1,1) PRIMARY KEY,
        TenFile                  NVARCHAR(255) NOT NULL,
        FilePath                 NVARCHAR(500) NOT NULL,
        ThuMuc                   NVARCHAR(255),
        Status                   NVARCHAR(50) DEFAULT 'pending',
        -- pending, classifying, extracting, embedding, pending_review,
        -- pending_retry, waiting_quota, publishing, published, failed, rejected
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
        FailureType              NVARCHAR(50)  NULL,
        NextRetryAt              DATETIME      NULL,
        ExtractionReport         NVARCHAR(MAX) NULL,
        QualityScore             INT           NULL,
        QualityStatus            NVARCHAR(50)  NULL,
        CreatedAt                DATETIME DEFAULT GETDATE(),
        UpdatedAt                DATETIME DEFAULT GETDATE()
    );
END
GO

-- Cot bo sung tu P1 (queue nang cao) — them rieng, idempotent
IF COL_LENGTH('dbo.IngestionJobs','Priority')   IS NULL ALTER TABLE dbo.IngestionJobs ADD Priority INT NOT NULL CONSTRAINT DF_IngestionJobs_Priority DEFAULT 100;
GO
IF COL_LENGTH('dbo.IngestionJobs','MaxPages')   IS NULL ALTER TABLE dbo.IngestionJobs ADD MaxPages INT NULL;
GO
IF COL_LENGTH('dbo.IngestionJobs','CanceledBy') IS NULL ALTER TABLE dbo.IngestionJobs ADD CanceledBy NVARCHAR(255) NULL;
GO
IF COL_LENGTH('dbo.IngestionJobs','CanceledAt') IS NULL ALTER TABLE dbo.IngestionJobs ADD CanceledAt DATETIME NULL;
GO

-- GD4: phan loai chon tu form upload (luu cung job de worker dung lam override)
IF COL_LENGTH('dbo.IngestionJobs','Domain')          IS NULL ALTER TABLE dbo.IngestionJobs ADD Domain NVARCHAR(50) NULL;
GO
IF COL_LENGTH('dbo.IngestionJobs','SecurityLevel')   IS NULL ALTER TABLE dbo.IngestionJobs ADD SecurityLevel NVARCHAR(20) NULL;
GO
IF COL_LENGTH('dbo.IngestionJobs','PhongBan')        IS NULL ALTER TABLE dbo.IngestionJobs ADD PhongBan NVARCHAR(50) NULL;
GO
IF COL_LENGTH('dbo.IngestionJobs','CongDoan')        IS NULL ALTER TABLE dbo.IngestionJobs ADD CongDoan NVARCHAR(100) NULL;
GO
IF COL_LENGTH('dbo.IngestionJobs','Site')            IS NULL ALTER TABLE dbo.IngestionJobs ADD Site NVARCHAR(100) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_IngestionJobs_Status_NextRetryAt' AND object_id = OBJECT_ID('dbo.IngestionJobs'))
    CREATE INDEX IX_IngestionJobs_Status_NextRetryAt ON dbo.IngestionJobs(Status, NextRetryAt);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_IngestionJobs_Status_Priority' AND object_id = OBJECT_ID('dbo.IngestionJobs'))
    CREATE INDEX IX_IngestionJobs_Status_Priority ON dbo.IngestionJobs(Status, Priority, CreatedAt);
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.DocumentFamily') AND type = 'U')
BEGIN
    CREATE TABLE dbo.DocumentFamily (
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
    CREATE UNIQUE INDEX UX_DocumentFamily_BaseCode ON dbo.DocumentFamily(BaseCode);
GO

-- TaiLieu: bang tai lieu trung tam (trung lap domain, KHONG con thien co khi)
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.TaiLieu') AND type = 'U')
BEGIN
    CREATE TABLE dbo.TaiLieu (
        DocID           INT IDENTITY(1,1) PRIMARY KEY,
        TenFile         NVARCHAR(255) NOT NULL,
        ThuMuc          NVARCHAR(255),                    -- folder goc luc nap (lich su)
        NgayTaiLen      DATETIME DEFAULT GETDATE(),
        TrangThaiVector BIT DEFAULT 0,
        TrangThai       NVARCHAR(50) DEFAULT 'published',  -- legacy
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
        CONSTRAINT FK_TaiLieu_Family
            FOREIGN KEY (FamilyID) REFERENCES dbo.DocumentFamily(FamilyID),
        CONSTRAINT CHK_LifecycleStatus
            CHECK (LifecycleStatus IN ('draft','published','archived','superseded','retired','rejected','deleting')),
        CONSTRAINT CHK_ReviewStatus
            CHECK (ReviewStatus IN ('pending_review','approved','rejected'))
    );
END
GO

-- >>> Cac cot phan loai/RBAC them RIENG bang ALTER idempotent <<<
-- (Day chinh la cho truoc day bi loi: dat trong CREATE bi guard nen DB cu khong nhan)
IF COL_LENGTH('dbo.TaiLieu','PhongBan')      IS NULL ALTER TABLE dbo.TaiLieu ADD PhongBan NVARCHAR(255) NULL;   -- = Departments.DeptCode (khoa phong ban duy nhat)
GO
IF COL_LENGTH('dbo.TaiLieu','Domain')        IS NULL ALTER TABLE dbo.TaiLieu ADD Domain NVARCHAR(50) NULL;        -- mechanical | tabular | generic
GO
IF COL_LENGTH('dbo.TaiLieu','SecurityLevel') IS NULL ALTER TABLE dbo.TaiLieu ADD SecurityLevel NVARCHAR(20) NOT NULL CONSTRAINT DF_TaiLieu_SecurityLevel DEFAULT 'internal';  -- public | internal | confidential
GO
IF COL_LENGTH('dbo.TaiLieu','CongDoan')      IS NULL ALTER TABLE dbo.TaiLieu ADD CongDoan NVARCHAR(100) NULL;     -- nhan to/cong doan (vd To_Han) — chi la nhan loc, KHONG phai phong ban
GO
IF COL_LENGTH('dbo.TaiLieu','Site')          IS NULL ALTER TABLE dbo.TaiLieu ADD Site NVARCHAR(100) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_BaseCode_Current' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_BaseCode_Current ON dbo.TaiLieu(BaseCode, IsCurrent, LifecycleStatus, ReviewStatus);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_BaseCode_Version' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_BaseCode_Version ON dbo.TaiLieu(BaseCode, VersionNo, VariantCode);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_Family_Variant_Current' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_Family_Variant_Current ON dbo.TaiLieu(FamilyID, VariantCode, IsCurrent);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_Site_Domain' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_Site_Domain ON dbo.TaiLieu(Site, Domain, ReviewStatus);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_PhongBan' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_PhongBan ON dbo.TaiLieu(PhongBan, ReviewStatus);
GO
-- Unique "ban hien hanh" — chi ap khi co BaseCode (tai lieu co khi). Tai lieu khong co
-- ma ban ve (BaseCode NULL) duoc loai khoi rang buoc nho dieu kien BaseCode IS NOT NULL.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_TaiLieu_Current_Per_Variant' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE UNIQUE INDEX UX_TaiLieu_Current_Per_Variant ON dbo.TaiLieu(BaseCode, VariantCode)
        WHERE IsCurrent = 1 AND LifecycleStatus = 'published' AND BaseCode IS NOT NULL;
GO

-- ==========================================================================
-- PHAN 2: DU LIEU DAC THU DOMAIN 'mechanical' (co khi)
--   Cac bang nay CHI dung cho domain mechanical. Domain tabular/generic
--   dung DocumentAttributes (PHAN 5B). Khi rebuild se gate theo DomainHandler.
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.TaiLieuKyThuat') AND type = 'U')
BEGIN
    CREATE TABLE dbo.TaiLieuKyThuat (
        ID              INT IDENTITY(1,1) PRIMARY KEY,
        DocID           INT,
        TrangSo         INT,
        LoaiTaiLieu     NVARCHAR(255),
        MaDoiTuong      NVARCHAR(MAX),
        TenSanPham      NVARCHAR(500),
        CongDoan        NVARCHAR(255),
        VatLieu         NVARCHAR(255),
        SoLuong         INT,
        NguoiLap        NVARCHAR(255),
        NgayVe          DATE,
        DungSaiDay      NVARCHAR(255),
        DungSaiKhac     NVARCHAR(255),
        KichThuocTongThe NVARCHAR(255),
        HDCV            NVARCHAR(MAX),
        YCKT            NVARCHAR(MAX),
        CONSTRAINT FK_TaiLieuKyThuat_TaiLieu
            FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID) ON DELETE CASCADE,
        CONSTRAINT UQ_TaiLieuKyThuat_Doc_Trang UNIQUE (DocID, TrangSo)
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.BangKeVatTu') AND type = 'U')
BEGIN
    CREATE TABLE dbo.BangKeVatTu (
        ID                 INT IDENTITY(1,1) PRIMARY KEY,
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
            FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID) ON DELETE CASCADE
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BangKeVatTu_DocID' AND object_id = OBJECT_ID('dbo.BangKeVatTu'))
    CREATE INDEX IX_BangKeVatTu_DocID ON dbo.BangKeVatTu(DocID);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BangKeVatTu_MaHang' AND object_id = OBJECT_ID('dbo.BangKeVatTu'))
    CREATE INDEX IX_BangKeVatTu_MaHang ON dbo.BangKeVatTu(MaHang);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BangKeVatTu_VatLieu' AND object_id = OBJECT_ID('dbo.BangKeVatTu'))
    CREATE INDEX IX_BangKeVatTu_VatLieu ON dbo.BangKeVatTu(VatLieu);
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.DocumentPages') AND type = 'U')
BEGIN
    CREATE TABLE dbo.DocumentPages (
        PageID             INT IDENTITY(1,1) PRIMARY KEY,
        DocID              INT NOT NULL,
        FileName           NVARCHAR(500) NOT NULL,
        PageNo             INT NOT NULL,
        TextExtract        NVARCHAR(MAX)  NULL,
        LocalOCRText       NVARCHAR(MAX)  NULL,
        VisionSummary      NVARCHAR(MAX)  NULL,
        LocalOCRConfidence FLOAT          NULL,
        ExtractionStatus   NVARCHAR(50)   NULL,
        ImagePath          NVARCHAR(1000) NULL,
        CreatedAt          DATETIME DEFAULT GETDATE(),
        UpdatedAt          DATETIME DEFAULT GETDATE()
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DocumentPages_DocID_PageNo' AND object_id = OBJECT_ID('dbo.DocumentPages'))
    CREATE INDEX IX_DocumentPages_DocID_PageNo ON dbo.DocumentPages(DocID, PageNo);
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.TechnicalAttributes') AND type = 'U')
BEGIN
    CREATE TABLE dbo.TechnicalAttributes (
        AttributeID    INT IDENTITY(1,1) PRIMARY KEY,
        DocID          INT NOT NULL,
        FileName       NVARCHAR(500) NOT NULL,
        PageNo         INT           NULL,
        AttributeType  NVARCHAR(100) NOT NULL,
        AttributeName  NVARCHAR(255) NULL,
        AttributeValue NVARCHAR(500) NOT NULL,
        Unit           NVARCHAR(50)  NULL,
        SourceText     NVARCHAR(MAX) NULL,
        Confidence     FLOAT         NULL,
        ExtractedBy    NVARCHAR(50)  NULL,
        HumanVerified  BIT DEFAULT 0,
        VerifiedBy     NVARCHAR(255) NULL,
        VerifiedAt     DATETIME      NULL,
        CreatedAt      DATETIME DEFAULT GETDATE()
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TechnicalAttributes_File_Type' AND object_id = OBJECT_ID('dbo.TechnicalAttributes'))
    CREATE INDEX IX_TechnicalAttributes_File_Type ON dbo.TechnicalAttributes(FileName, AttributeType);
GO

-- ==========================================================================
-- PHAN 3: LICH SU CHAT & PHAN HOI
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.LichSuChat') AND type = 'U')
BEGIN
    CREATE TABLE dbo.LichSuChat (
        ChatID        INT IDENTITY(1,1) PRIMARY KEY,
        SessionID     VARCHAR(100)  NOT NULL,
        CauHoi_User   NVARCHAR(MAX) NOT NULL,
        TraLoi_Bot    NVARCHAR(MAX) NOT NULL,
        HinhAnhUpload NVARCHAR(500),
        RefImages     NVARCHAR(MAX),
        DanhGia       SMALLINT,                 -- 1=Like, -1=Dislike, NULL=chua (SMALLINT vi can luu -1)
        ThoiGian      DATETIME DEFAULT GETDATE(),
        Username      NVARCHAR(255) NULL        -- tach lich su chat theo user
    );
END
GO
IF COL_LENGTH('dbo.LichSuChat','Username') IS NULL ALTER TABLE dbo.LichSuChat ADD Username NVARCHAR(255) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_LichSuChat_Session_Time' AND object_id = OBJECT_ID('dbo.LichSuChat'))
    CREATE NONCLUSTERED INDEX IX_LichSuChat_Session_Time ON dbo.LichSuChat(SessionID, ThoiGian);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_LichSuChat_Username_Time' AND object_id = OBJECT_ID('dbo.LichSuChat'))
    CREATE NONCLUSTERED INDEX IX_LichSuChat_Username_Time ON dbo.LichSuChat(Username, ThoiGian DESC);
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.FeedbackReview') AND type = 'U')
BEGIN
    CREATE TABLE dbo.FeedbackReview (
        FeedbackID         INT IDENTITY(1,1) PRIMARY KEY,
        ChatID             INT NOT NULL,
        Question           NVARCHAR(MAX),
        BotAnswer          NVARCHAR(MAX),
        FailureType        NVARCHAR(100),
        CorrectAnswer      NVARCHAR(MAX),
        CorrectSourceDocID INT NULL,
        ReviewerNote       NVARCHAR(MAX),
        AddedToGoldenSet   BIT DEFAULT 0,
        CreatedAt          DATETIME DEFAULT GETDATE(),
        SourceDocID        INT NULL,
        DocVersionNo       INT NULL,
        ContextHash        NVARCHAR(64) NULL,
        Department         NVARCHAR(100) NULL,
        Site               NVARCHAR(100) NULL,
        IsStale            BIT NOT NULL CONSTRAINT DF_FeedbackReview_IsStale DEFAULT 0,
        ResolvedByDocID    INT NULL,
        ResolvedAt         DATETIME NULL,
        CONSTRAINT FK_FeedbackReview_ChatID
            FOREIGN KEY (ChatID) REFERENCES dbo.LichSuChat(ChatID) ON DELETE CASCADE
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.AnswerSource') AND type = 'U')
BEGIN
    CREATE TABLE dbo.AnswerSource (
        SourceID    INT IDENTITY(1,1) PRIMARY KEY,
        ChatID      INT NOT NULL,
        DocID       INT NULL,
        FileName    NVARCHAR(500) NULL,
        VersionNo   INT NULL,
        VariantCode NVARCHAR(100) NULL,
        ChunkRef    NVARCHAR(200) NULL,
        Score       FLOAT NULL,
        RankNo      INT NULL,
        IsCurrent   BIT NULL,
        CreatedAt   DATETIME DEFAULT GETDATE(),
        CONSTRAINT FK_AnswerSource_ChatID
            FOREIGN KEY (ChatID) REFERENCES dbo.LichSuChat(ChatID) ON DELETE CASCADE
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.DocQualityScore') AND type = 'U')
BEGIN
    CREATE TABLE dbo.DocQualityScore (
        DocID           INT NOT NULL PRIMARY KEY,
        LikeCount       INT NOT NULL DEFAULT 0,
        DislikeCount    INT NOT NULL DEFAULT 0,
        WeightedLike    FLOAT NOT NULL DEFAULT 0,
        WeightedDislike FLOAT NOT NULL DEFAULT 0,
        QualityScore    FLOAT NULL,
        NetScore        FLOAT NULL,
        SampleSize      INT NOT NULL DEFAULT 0,
        LastComputedAt  DATETIME NULL,
        CONSTRAINT FK_DocQualityScore_DocID FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID) ON DELETE CASCADE
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.GoldenAnswer') AND type = 'U')
BEGIN
    CREATE TABLE dbo.GoldenAnswer (
        GoldenID      INT IDENTITY(1,1) PRIMARY KEY,
        FeedbackID    INT NULL,
        QuestionHash  NVARCHAR(64) NOT NULL,
        QuestionText  NVARCHAR(4000) NULL,
        GoldenAnswer  NVARCHAR(MAX) NULL,
        SourceDocID   INT NULL,
        Department    NVARCHAR(100) NULL,
        Site          NVARCHAR(100) NULL,
        CreatedBy     NVARCHAR(256) NULL,
        IsActive      BIT NOT NULL DEFAULT 1,
        CreatedAt     DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.RegressionQuestion') AND type = 'U')
BEGIN
    CREATE TABLE dbo.RegressionQuestion (
        RegQID           INT IDENTITY(1,1) PRIMARY KEY,
        QuestionText     NVARCHAR(2000) NOT NULL,
        ExpectedDocID    INT NULL,
        ExpectedKeywords NVARCHAR(MAX) NULL,
        Department       NVARCHAR(100) NULL,
        Site             NVARCHAR(100) NULL,
        CreatedBy        NVARCHAR(256) NULL,
        IsActive         BIT NOT NULL DEFAULT 1,
        CreatedAt        DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.RegressionRun') AND type = 'U')
BEGIN
    CREATE TABLE dbo.RegressionRun (
        RunID         INT IDENTITY(1,1) PRIMARY KEY,
        RegQID        INT NOT NULL,
        RunBatchID    NVARCHAR(64) NOT NULL,
        AnswerText    NVARCHAR(MAX) NULL,
        MatchedDocIDs NVARCHAR(500) NULL,
        DocHit        BIT NOT NULL DEFAULT 0,
        KeywordHit    BIT NOT NULL DEFAULT 0,
        Passed        BIT NOT NULL DEFAULT 0,
        DurationMs    INT NULL,
        ErrorText     NVARCHAR(1000) NULL,
        CreatedAt     DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_RegressionRun_RegQID FOREIGN KEY (RegQID) REFERENCES dbo.RegressionQuestion(RegQID) ON DELETE CASCADE
    );
END
GO

-- ==========================================================================
-- PHAN 4: AUTH & ROLES
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.Users') AND type = 'U')
BEGIN
    CREATE TABLE dbo.Users (
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
    CREATE TABLE dbo.Roles (
        RoleID   INT IDENTITY(1,1) PRIMARY KEY,
        RoleName NVARCHAR(100) UNIQUE NOT NULL
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.UserRoles') AND type = 'U')
BEGIN
    CREATE TABLE dbo.UserRoles (
        UserID INT,
        RoleID INT,
        PRIMARY KEY (UserID, RoleID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.UserDepartments') AND type = 'U')
BEGIN
    CREATE TABLE dbo.UserDepartments (
        UserID     INT NOT NULL,
        Department NVARCHAR(255) NOT NULL,   -- = Departments.DeptCode
        PRIMARY KEY (UserID, Department)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.UserSecurityClearance') AND type = 'U')
BEGIN
    CREATE TABLE dbo.UserSecurityClearance (
        UserID   INT NOT NULL PRIMARY KEY,
        MaxLevel NVARCHAR(20) NOT NULL DEFAULT 'internal',  -- public | internal | confidential
        CONSTRAINT FK_USC_Users FOREIGN KEY (UserID) REFERENCES dbo.Users(UserID)
    );
END
GO

-- ==========================================================================
-- PHAN 4B: DANH MUC PHONG BAN / SITE (data-driven, P1)
--   Departments.DeptCode = KHOA PHONG BAN DUY NHAT cua he thong.
--   TaiLieu.PhongBan, UserDepartments.Department deu tham chieu khoa nay.
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.Departments') AND type = 'U')
BEGIN
    CREATE TABLE dbo.Departments (
        DeptCode  NVARCHAR(255) NOT NULL PRIMARY KEY,   -- vd: Production, Accountant, HR
        DeptName  NVARCHAR(255) NULL,                   -- ten hien thi
        Domain    NVARCHAR(50)  NULL,                   -- mechanical | tabular | generic
        Site      NVARCHAR(100) NULL,
        IsActive  BIT NOT NULL DEFAULT 1,
        CreatedAt DATETIME DEFAULT GETDATE()
    );
END
GO
-- Cot bo sung cho mo hinh da phong ban (them rieng, idempotent)
IF COL_LENGTH('dbo.Departments','DefaultSecurity') IS NULL ALTER TABLE dbo.Departments ADD DefaultSecurity NVARCHAR(20) NOT NULL CONSTRAINT DF_Departments_DefaultSecurity DEFAULT 'internal';  -- public|internal|confidential
GO
IF COL_LENGTH('dbo.Departments','FolderGoc')       IS NULL ALTER TABLE dbo.Departments ADD FolderGoc NVARCHAR(150) NULL;  -- vd '08.Production'
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.Sites') AND type = 'U')
BEGIN
    CREATE TABLE dbo.Sites (
        SiteCode  NVARCHAR(100) NOT NULL PRIMARY KEY,
        SiteName  NVARCHAR(255) NULL,
        IsActive  BIT NOT NULL DEFAULT 1,
        CreatedAt DATETIME DEFAULT GETDATE()
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.UserSites') AND type = 'U')
BEGIN
    CREATE TABLE dbo.UserSites (
        UserID INT NOT NULL,
        Site   NVARCHAR(100) NOT NULL,
        PRIMARY KEY (UserID, Site)
    );
END
GO

-- ==========================================================================
-- PHAN 5: AUDIT LOG
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.AuditLog') AND type = 'U')
BEGIN
    CREATE TABLE dbo.AuditLog (
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

-- ==========================================================================
-- PHAN 5B: METADATA TONG QUAT (domain tabular/generic)
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.DocumentAttributes') AND type = 'U')
BEGIN
    CREATE TABLE dbo.DocumentAttributes (
        AttrID         INT IDENTITY(1,1) PRIMARY KEY,
        DocID          INT NOT NULL,
        Domain         NVARCHAR(50)  NOT NULL,   -- mechanical | tabular | generic
        AttributeKey   NVARCHAR(150) NOT NULL,
        AttributeValue NVARCHAR(MAX) NULL,
        Confidence     FLOAT NULL,
        ExtractedBy    NVARCHAR(50) NULL,        -- regex | llm | manual
        CreatedAt      DATETIME DEFAULT GETDATE(),
        CONSTRAINT FK_DocAttr_TaiLieu FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID) ON DELETE CASCADE
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DocAttr_Doc_Domain' AND object_id = OBJECT_ID('dbo.DocumentAttributes'))
    CREATE INDEX IX_DocAttr_Doc_Domain ON dbo.DocumentAttributes(DocID, Domain);
GO

-- ==========================================================================
-- PHAN 6: TU DIEN VAT TU (domain mechanical, P2)
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.MaterialDictionary') AND type = 'U')
BEGIN
    CREATE TABLE dbo.MaterialDictionary (
        MaterialID    INT IDENTITY(1,1) PRIMARY KEY,
        CanonicalCode NVARCHAR(100) NOT NULL,
        DisplayName   NVARCHAR(255) NOT NULL,
        Category      NVARCHAR(100) NULL,
        IsActive      BIT NOT NULL DEFAULT 1,
        CreatedAt     DATETIME NOT NULL DEFAULT GETDATE(),
        UpdatedAt     DATETIME NULL,
        CONSTRAINT UQ_MaterialDictionary_Code UNIQUE (CanonicalCode)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.MaterialSynonym') AND type = 'U')
BEGIN
    CREATE TABLE dbo.MaterialSynonym (
        SynonymID  INT IDENTITY(1,1) PRIMARY KEY,
        MaterialID INT NOT NULL,
        Synonym    NVARCHAR(255) NOT NULL,
        IsActive   BIT NOT NULL DEFAULT 1,
        CreatedAt  DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_MaterialSynonym_Material FOREIGN KEY (MaterialID)
            REFERENCES dbo.MaterialDictionary(MaterialID) ON DELETE CASCADE
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MaterialSynonym_Synonym' AND object_id = OBJECT_ID(N'dbo.MaterialSynonym'))
    CREATE INDEX IX_MaterialSynonym_Synonym ON dbo.MaterialSynonym(Synonym);
GO

-- ==========================================================================
-- PHAN P0: METADATA TONG QUAT DA PHONG BAN (V0004 inline vao baseline)
-- Cot moi them rieng bang ALTER idempotent (KHONG dat trong CREATE bi guard).
-- ==========================================================================
IF COL_LENGTH('dbo.TaiLieu','Title')           IS NULL ALTER TABLE dbo.TaiLieu ADD Title NVARCHAR(500) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','Summary')         IS NULL ALTER TABLE dbo.TaiLieu ADD Summary NVARCHAR(MAX) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','Tags')            IS NULL ALTER TABLE dbo.TaiLieu ADD Tags NVARCHAR(1000) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','DocNumber')       IS NULL ALTER TABLE dbo.TaiLieu ADD DocNumber NVARCHAR(150) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','IssuedDate')      IS NULL ALTER TABLE dbo.TaiLieu ADD IssuedDate DATE NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','EffectiveDate')   IS NULL ALTER TABLE dbo.TaiLieu ADD EffectiveDate DATE NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','ExpiryDate')      IS NULL ALTER TABLE dbo.TaiLieu ADD ExpiryDate DATE NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','ReviewDate')      IS NULL ALTER TABLE dbo.TaiLieu ADD ReviewDate DATE NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','OwnerSigner')     IS NULL ALTER TABLE dbo.TaiLieu ADD OwnerSigner NVARCHAR(255) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','DocLanguage')     IS NULL ALTER TABLE dbo.TaiLieu ADD DocLanguage NVARCHAR(20) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu','EffectiveStatus') IS NULL ALTER TABLE dbo.TaiLieu ADD EffectiveStatus NVARCHAR(20) NOT NULL CONSTRAINT DF_TaiLieu_EffectiveStatus DEFAULT 'active';
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_DocNumber' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_DocNumber ON dbo.TaiLieu(DocNumber);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_EffectiveStatus' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_EffectiveStatus ON dbo.TaiLieu(EffectiveStatus, ExpiryDate);
GO
IF COL_LENGTH('dbo.IngestionJobs','UploadMetaJson') IS NULL ALTER TABLE dbo.IngestionJobs ADD UploadMetaJson NVARCHAR(MAX) NULL;
GO

-- ==========================================================================
-- PHAN P1.4: CAU HINH UNG DUNG (AppSettings)
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'AppSettings' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.AppSettings (
        SettingKey   NVARCHAR(100) NOT NULL PRIMARY KEY,
        SettingValue NVARCHAR(MAX) NULL,
        UpdatedAt    DATETIME NOT NULL CONSTRAINT DF_AppSettings_UpdatedAt DEFAULT GETDATE(),
        UpdatedBy    NVARCHAR(255) NULL
    );
END
GO

MERGE dbo.AppSettings AS tgt
USING (VALUES
    ('expiry_warning_days', '30'),
    ('rag_general_top_k', '30')
) AS src (SettingKey, SettingValue)
ON tgt.SettingKey = src.SettingKey
WHEN NOT MATCHED BY TARGET THEN
    INSERT (SettingKey, SettingValue) VALUES (src.SettingKey, src.SettingValue);
GO

-- ==========================================================================
-- GHI NHAN PHIEN BAN BASELINE
-- ==========================================================================
IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'baseline_v2')
    INSERT INTO dbo._SchemaVersions (Version, Description)
    VALUES ('baseline_v2', 'Hop nhat init + P0..P3 + P1 multi-site; cot moi qua ALTER idempotent; trung lap domain');
GO

PRINT 'Baseline v2 hoan tat.';
GO
