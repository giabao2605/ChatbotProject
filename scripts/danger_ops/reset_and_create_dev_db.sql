-- ============================================================
-- !!! CANH BAO - DANGER ZONE !!!
-- ============================================================
--
-- Script: scripts/danger_ops/reset_and_create_dev_db.sql
--
-- MUC DICH: Reset toan bo database ve trang thai ban dau.
--           Dung cho moi truong PHAT TRIEN (development) va TEST.
--
-- TAC DONG:
--   [x] XOA VINH VIEN toan bo du lieu trong tat ca bang
--   [x] XOA VINH VIEN tat ca bang (Users, TaiLieu, LichSuChat, ...)
--   [x] Tao lai co cau bang tu dau
--   [x] Seed du lieu mau (seed data)
--
-- TUYET DOI KHONG CHAY SCRIPT NAY TREN:
--   - Moi truong Production
--   - Moi truong Staging co du lieu thuc
--   - Server co du lieu cua khach hang / cong ty
--
-- CHI CHAY KHI:
--   - Phat trien local tren may ca nhan
--   - CI/CD pipeline voi DB test rieng biet
--   - Co su xac nhan cua truong nhom phat trien
--
-- De khoi tao DB an toan (khong mat du lieu), dung:
--   database/init/Mech_Chatbot_DB.sql
-- ============================================================

-- Xac nhan nguoi dung nhan thuc ro rang ro rui (bo comment dong duoi de cho phep chay)
-- RAISERROR('STOP: Script nay se xoa toan bo du lieu. Bo comment dong nay de xac nhan.', 20, 1) WITH LOG;

USE master;
GO

-- Tao database neu chua ton tai
IF NOT EXISTS (
    SELECT 1 FROM sys.databases WHERE name = 'Mech_Chatbot_DB'
)
BEGIN
    CREATE DATABASE Mech_Chatbot_DB;
END
GO

USE Mech_Chatbot_DB;
GO

PRINT '=== [DANGER] Bat dau xoa toan bo du lieu va bang... ===';

-- Buoc 1: Xoa tat ca Foreign Key Constraints truoc
WHILE (
    EXISTS (
        SELECT 1
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_TYPE = 'FOREIGN KEY'
    )
)
BEGIN
    DECLARE @fk_sql NVARCHAR(2000);
    SELECT TOP 1 @fk_sql = (
        'ALTER TABLE ' + TABLE_SCHEMA + '.[' + TABLE_NAME + '] DROP CONSTRAINT [' + CONSTRAINT_NAME + ']'
    )
    FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_TYPE = 'FOREIGN KEY';
    EXEC (@fk_sql);
END
GO

-- Buoc 2: Xoa tat ca bang (thu tu phu thuoc)
IF OBJECT_ID('dbo.AuditLog',        'U') IS NOT NULL DROP TABLE dbo.AuditLog;
IF OBJECT_ID('dbo.DocQualityScore', 'U') IS NOT NULL DROP TABLE dbo.DocQualityScore;
IF OBJECT_ID('dbo.GoldenAnswer', 'U') IS NOT NULL DROP TABLE dbo.GoldenAnswer;
IF OBJECT_ID('dbo.AnswerSource',  'U') IS NOT NULL DROP TABLE dbo.AnswerSource;
IF OBJECT_ID('dbo.FeedbackReview',  'U') IS NOT NULL DROP TABLE dbo.FeedbackReview;
IF OBJECT_ID('dbo.LichSuChat',      'U') IS NOT NULL DROP TABLE dbo.LichSuChat;
IF OBJECT_ID('dbo.BangKeVatTu',     'U') IS NOT NULL DROP TABLE dbo.BangKeVatTu;
IF OBJECT_ID('dbo.TaiLieuKyThuat',  'U') IS NOT NULL DROP TABLE dbo.TaiLieuKyThuat;
IF OBJECT_ID('dbo.TaiLieu',             'U') IS NOT NULL DROP TABLE dbo.TaiLieu;
IF OBJECT_ID('dbo.DocumentFamily',      'U') IS NOT NULL DROP TABLE dbo.DocumentFamily;
IF OBJECT_ID('dbo.DocumentPages',       'U') IS NOT NULL DROP TABLE dbo.DocumentPages;
IF OBJECT_ID('dbo.TechnicalAttributes', 'U') IS NOT NULL DROP TABLE dbo.TechnicalAttributes;
IF OBJECT_ID('dbo.IngestionJobs',       'U') IS NOT NULL DROP TABLE dbo.IngestionJobs;
IF OBJECT_ID('dbo.UserDepartments',     'U') IS NOT NULL DROP TABLE dbo.UserDepartments;
IF OBJECT_ID('dbo.UserRoles',           'U') IS NOT NULL DROP TABLE dbo.UserRoles;
IF OBJECT_ID('dbo.Roles',               'U') IS NOT NULL DROP TABLE dbo.Roles;
IF OBJECT_ID('dbo.Users',               'U') IS NOT NULL DROP TABLE dbo.Users;
GO

PRINT '=== [DANGER] Da xoa xong tat ca bang. Bat dau tao lai... ===';

-- ==========================================
-- PHAN 1: QUAN LY TAI LIEU & QUEUE
-- ==========================================

CREATE TABLE IngestionJobs (
    JobID                    INT IDENTITY(1, 1) PRIMARY KEY,
    TenFile                  NVARCHAR(255) NOT NULL,
    FilePath                 NVARCHAR(500) NOT NULL,
    ThuMuc                   NVARCHAR(255),
    Status                   NVARCHAR(50) DEFAULT 'pending',
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
    FailureType              NVARCHAR(50)  NULL,
    NextRetryAt              DATETIME      NULL,
    ExtractionReport         NVARCHAR(MAX) NULL,
    QualityScore             INT           NULL,
    QualityStatus            NVARCHAR(50)  NULL,
    CreatedAt                DATETIME DEFAULT GETDATE(),
    UpdatedAt                DATETIME DEFAULT GETDATE()
);
GO

CREATE TABLE DocumentFamily (
    FamilyID    INT IDENTITY(1,1) PRIMARY KEY,
    BaseCode    NVARCHAR(255) NOT NULL,
    FamilyName  NVARCHAR(500),
    Department  NVARCHAR(255),
    Description NVARCHAR(MAX),
    CreatedAt   DATETIME DEFAULT GETDATE(),
    UpdatedAt   DATETIME DEFAULT GETDATE()
);
GO

CREATE UNIQUE INDEX UX_DocumentFamily_BaseCode ON DocumentFamily(BaseCode);
GO

CREATE TABLE TaiLieu (
    DocID           INT IDENTITY(1, 1) PRIMARY KEY,
    TenFile         NVARCHAR(255) NOT NULL,
    ThuMuc          NVARCHAR(255),
    NgayTaiLen      DATETIME DEFAULT GETDATE(),
    TrangThaiVector BIT DEFAULT 0,
    TrangThai       NVARCHAR(50) DEFAULT 'published',
    NgayDuyet       DATETIME,
    NguoiDuyet      NVARCHAR(255),
    LyDoTuChoi      NVARCHAR(MAX),
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
        FOREIGN KEY (FamilyID) REFERENCES DocumentFamily(FamilyID),
    CONSTRAINT CHK_LifecycleStatus
        CHECK (LifecycleStatus IN ('draft', 'published', 'archived', 'superseded', 'retired', 'rejected', 'deleting')),
    CONSTRAINT CHK_ReviewStatus
        CHECK (ReviewStatus IN ('pending_review', 'approved', 'rejected'))
);
GO

CREATE INDEX IX_TaiLieu_BaseCode_Current       ON TaiLieu(BaseCode, IsCurrent, LifecycleStatus, ReviewStatus);
CREATE INDEX IX_TaiLieu_BaseCode_Version        ON TaiLieu(BaseCode, VersionNo, VariantCode);
CREATE INDEX IX_TaiLieu_Family_Variant_Current  ON TaiLieu(FamilyID, VariantCode, IsCurrent);
CREATE UNIQUE INDEX UX_TaiLieu_Current_Per_Variant ON TaiLieu(BaseCode, VariantCode)
    WHERE IsCurrent = 1 AND LifecycleStatus = 'published';
GO

-- ==========================================
-- PHAN 2: DU LIEU KY THUAT CO KHI
-- ==========================================

CREATE TABLE TaiLieuKyThuat (
    ID              INT IDENTITY(1, 1) PRIMARY KEY,
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
        FOREIGN KEY (DocID) REFERENCES TaiLieu(DocID) ON DELETE CASCADE,
    CONSTRAINT UQ_TaiLieuKyThuat_Doc_Trang
        UNIQUE (DocID, TrangSo)
);
GO

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
GO

CREATE INDEX IX_BangKeVatTu_DocID   ON BangKeVatTu(DocID);
CREATE INDEX IX_BangKeVatTu_MaHang  ON BangKeVatTu(MaHang);
CREATE INDEX IX_BangKeVatTu_VatLieu ON BangKeVatTu(VatLieu);
CREATE INDEX IX_IngestionJobs_Status_NextRetryAt ON IngestionJobs(Status, NextRetryAt);
GO

-- ==========================================
-- PHAN 2B: TRANG TAI LIEU & THUOC TINH KY THUAT
-- ==========================================

CREATE TABLE DocumentPages (
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
GO

CREATE INDEX IX_DocumentPages_DocID_PageNo ON DocumentPages(DocID, PageNo);
GO

CREATE TABLE TechnicalAttributes (
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
GO

CREATE INDEX IX_TechnicalAttributes_File_Type ON TechnicalAttributes(FileName, AttributeType);
GO

-- ==========================================
-- PHAN 3: LICH SU CHAT
-- ==========================================

CREATE TABLE LichSuChat (
    ChatID        INT IDENTITY(1, 1) PRIMARY KEY,
    SessionID     VARCHAR(100)  NOT NULL,
    CauHoi_User   NVARCHAR(MAX) NOT NULL,
    TraLoi_Bot    NVARCHAR(MAX) NOT NULL,
    HinhAnhUpload NVARCHAR(500),
    RefImages     NVARCHAR(MAX),
    DanhGia       SMALLINT,
    ThoiGian      DATETIME DEFAULT GETDATE(),
    Username      NVARCHAR(255) NOT NULL     -- Bat buoc: phan tach lich su chat theo user
);
GO

CREATE TABLE DocQualityScore (
    DocID            INT NOT NULL PRIMARY KEY,
    LikeCount        INT NOT NULL DEFAULT 0,
    DislikeCount     INT NOT NULL DEFAULT 0,
    WeightedLike     FLOAT NOT NULL DEFAULT 0,
    WeightedDislike  FLOAT NOT NULL DEFAULT 0,
    QualityScore     FLOAT NULL,
    NetScore         FLOAT NULL,
    SampleSize       INT NOT NULL DEFAULT 0,
    LastComputedAt   DATETIME NULL,
    CONSTRAINT FK_DocQualityScore_DocID FOREIGN KEY (DocID) REFERENCES TaiLieu(DocID) ON DELETE CASCADE
);
GO

CREATE TABLE GoldenAnswer (
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
GO

CREATE TABLE AnswerSource (
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
        FOREIGN KEY (ChatID) REFERENCES LichSuChat(ChatID) ON DELETE CASCADE
);
GO

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
    SourceDocID       INT NULL,
    DocVersionNo      INT NULL,
    ContextHash       NVARCHAR(64) NULL,
    Department        NVARCHAR(100) NULL,
    Site              NVARCHAR(100) NULL,
    IsStale           BIT NOT NULL DEFAULT 0,
    ResolvedByDocID   INT NULL,
    ResolvedAt        DATETIME NULL,
    CONSTRAINT FK_FeedbackReview_ChatID
        FOREIGN KEY (ChatID) REFERENCES LichSuChat(ChatID) ON DELETE CASCADE
);
GO

CREATE NONCLUSTERED INDEX IX_LichSuChat_Session_Time ON LichSuChat(SessionID, ThoiGian);
CREATE NONCLUSTERED INDEX IX_LichSuChat_Username_Time ON LichSuChat(Username, ThoiGian DESC);
GO

-- ==========================================
-- PHAN 4: AUTH & ROLES
-- ==========================================

CREATE TABLE Users (
    UserID       INT IDENTITY(1,1) PRIMARY KEY,
    Username     NVARCHAR(255) UNIQUE NOT NULL,
    PasswordHash NVARCHAR(500) NOT NULL,
    DisplayName  NVARCHAR(255),
    Department   NVARCHAR(255),
    IsActive     BIT DEFAULT 1,
    CreatedAt    DATETIME DEFAULT GETDATE()
);
GO

CREATE TABLE Roles (
    RoleID   INT IDENTITY(1,1) PRIMARY KEY,
    RoleName NVARCHAR(100) UNIQUE NOT NULL
);
GO

CREATE TABLE UserRoles (
    UserID INT,
    RoleID INT,
    PRIMARY KEY (UserID, RoleID)
);
GO

CREATE TABLE UserDepartments (
    UserID     INT NOT NULL,
    Department NVARCHAR(255) NOT NULL,
    PRIMARY KEY (UserID, Department)
);
GO

-- Seed du lieu mau (DEV ONLY)
INSERT INTO Roles (RoleName) VALUES ('admin'), ('reviewer'), ('uploader'), ('viewer');

INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
VALUES ('admin', '$2b$12$GjF79FWNuuNfl4VWOA28iOk4ubZWWd5OltSsAiZ5TgaWPz5UtAZpu', 'Administrator', 'IT');
INSERT INTO UserRoles VALUES (1, 1);

INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
VALUES ('viewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Nhan Vien A', 'Tu_Hoc');
INSERT INTO UserRoles VALUES (2, 4);

INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
VALUES ('uploader1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Uploader', 'Ky_Thuat');
INSERT INTO UserRoles VALUES (3, 3);

INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
VALUES ('reviewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Truong Phong', 'Ky_Thuat');
INSERT INTO UserRoles VALUES (4, 2);

INSERT INTO UserDepartments VALUES (1,'IT'), (2,'Tu_Hoc'), (3,'Ky_Thuat'), (4,'Ky_Thuat');
GO

-- ==========================================
-- PHAN 5: AUDIT LOG
-- ==========================================

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
GO

PRINT '=== [DONE] Reset hoan tat. DB da duoc tao lai voi du lieu mau. ===';
SELECT DB_NAME() AS [Database], GETDATE() AS [Thoi gian reset];
GO
