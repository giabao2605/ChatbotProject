-- ==========================================
-- DATABASE HO TRO CHATBOT CO KHI (da sua - them cot RefImages cho FIX C5)
-- ==========================================
IF NOT EXISTS (
    SELECT *
    FROM sys.databases
    WHERE name = 'Mech_Chatbot_DB'
) BEGIN CREATE DATABASE Mech_Chatbot_DB;
END
GO USE Mech_Chatbot_DB;
GO -- Xoa Foreign keys neu co
    WHILE (
        EXISTS (
            SELECT 1
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
            WHERE CONSTRAINT_TYPE = 'FOREIGN KEY'
        )
    ) BEGIN
DECLARE @sql NVARCHAR(2000);
SELECT TOP 1 @sql = (
        'ALTER TABLE ' + TABLE_SCHEMA + '.[' + TABLE_NAME + '] DROP CONSTRAINT [' + CONSTRAINT_NAME + ']'
    )
FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
WHERE CONSTRAINT_TYPE = 'FOREIGN KEY';
EXEC (@sql);
END
GO -- Xoa cac bang cu (dung thu tu phu thuoc)
IF OBJECT_ID('dbo.LichSuChat', 'U') IS NOT NULL DROP TABLE dbo.LichSuChat;
IF OBJECT_ID('dbo.BangKeVatTu', 'U') IS NOT NULL DROP TABLE dbo.BangKeVatTu;
IF OBJECT_ID('dbo.TaiLieuKyThuat', 'U') IS NOT NULL DROP TABLE dbo.TaiLieuKyThuat;
IF OBJECT_ID('dbo.TaiLieu', 'U') IS NOT NULL DROP TABLE dbo.TaiLieu;
IF OBJECT_ID('dbo.DocumentFamily', 'U') IS NOT NULL DROP TABLE dbo.DocumentFamily;
IF OBJECT_ID('dbo.IngestionJobs', 'U') IS NOT NULL DROP TABLE dbo.IngestionJobs;
IF OBJECT_ID('dbo.FeedbackReview', 'U') IS NOT NULL DROP TABLE dbo.FeedbackReview;
IF OBJECT_ID('dbo.UserRoles', 'U') IS NOT NULL DROP TABLE dbo.UserRoles;
IF OBJECT_ID('dbo.Roles', 'U') IS NOT NULL DROP TABLE dbo.Roles;
IF OBJECT_ID('dbo.Users', 'U') IS NOT NULL DROP TABLE dbo.Users;
GO -- ==========================================
    -- PHAN 1: QUAN LY TAI LIEU (Documents) & QUEUE
    -- ==========================================
    CREATE TABLE IngestionJobs (
        JobID INT IDENTITY(1, 1) PRIMARY KEY,
        TenFile NVARCHAR(255) NOT NULL,
        FilePath NVARCHAR(500) NOT NULL,
        ThuMuc NVARCHAR(255),
        Status NVARCHAR(50) DEFAULT 'pending', -- pending, classifying, extracting, embedding, failed, pending_review, published, rejected
        ErrorMessage NVARCHAR(MAX),
        UploadedBy NVARCHAR(255) NULL,
        RequestedAction NVARCHAR(50) NULL,
        ClassificationJson NVARCHAR(MAX) NULL,
        ClassificationConfidence FLOAT NULL,
        RetryCount INT DEFAULT 0,
        MaxRetry INT DEFAULT 3,
        LockedBy NVARCHAR(255) NULL,
        LockedAt DATETIME NULL,
        ProgressPercent INT DEFAULT 0,
        CreatedAt DATETIME DEFAULT GETDATE(),
        UpdatedAt DATETIME DEFAULT GETDATE()
    );
GO
    CREATE TABLE DocumentFamily (
        FamilyID INT IDENTITY(1,1) PRIMARY KEY,
        BaseCode NVARCHAR(255) NOT NULL,
        FamilyName NVARCHAR(500),
        Department NVARCHAR(255),
        Description NVARCHAR(MAX),
        CreatedAt DATETIME DEFAULT GETDATE(),
        UpdatedAt DATETIME DEFAULT GETDATE()
    );
GO
    CREATE UNIQUE INDEX UX_DocumentFamily_BaseCode ON DocumentFamily(BaseCode);
GO

    CREATE TABLE TaiLieu (
        DocID INT IDENTITY(1, 1) PRIMARY KEY,
        TenFile NVARCHAR(255) NOT NULL,
        ThuMuc NVARCHAR(255),
        NgayTaiLen DATETIME DEFAULT GETDATE(),
        TrangThaiVector BIT DEFAULT 0,
        TrangThai NVARCHAR(50) DEFAULT 'published', -- Legacy field
        NgayDuyet DATETIME,
        NguoiDuyet NVARCHAR(255),
        LyDoTuChoi NVARCHAR(MAX),
        -- New versioning & lifecycle fields
        FamilyID INT NULL,
        BaseCode NVARCHAR(255) NULL,
        VersionNo INT NULL,
        VersionLabel NVARCHAR(50) NULL,
        VariantCode NVARCHAR(255) DEFAULT 'default',
        VariantGroup NVARCHAR(255) NULL,
        LifecycleStatus NVARCHAR(50) DEFAULT 'draft',
        ReviewStatus NVARCHAR(50) DEFAULT 'pending_review',
        IsCurrent BIT DEFAULT 0,
        IsArchived BIT DEFAULT 0,
        SupersedesDocID INT NULL,
        PublishedAt DATETIME NULL,
        ArchivedAt DATETIME NULL,
        UploadedBy NVARCHAR(255) NULL,
        ReviewedBy NVARCHAR(255) NULL,
        ClassificationConfidence FLOAT NULL,
        ClassificationJson NVARCHAR(MAX) NULL,
        CONSTRAINT FK_TaiLieu_Family FOREIGN KEY (FamilyID) REFERENCES DocumentFamily(FamilyID)
    );
GO
    CREATE INDEX IX_TaiLieu_BaseCode_Current ON TaiLieu(BaseCode, IsCurrent, LifecycleStatus, ReviewStatus);
    CREATE INDEX IX_TaiLieu_BaseCode_Version ON TaiLieu(BaseCode, VersionNo, VariantCode);
    CREATE INDEX IX_TaiLieu_Family_Variant_Current ON TaiLieu(FamilyID, VariantCode, IsCurrent);
    CREATE UNIQUE INDEX UX_TaiLieu_Current_Per_Variant ON TaiLieu(BaseCode, VariantCode) WHERE IsCurrent = 1 AND LifecycleStatus = 'published';
GO -- ==========================================
    -- PHAN 2: DU LIEU KY THUAT CO KHI
    -- ==========================================
    CREATE TABLE TaiLieuKyThuat (
        ID INT IDENTITY(1, 1) PRIMARY KEY,
        DocID INT,
        TrangSo INT,
        LoaiTaiLieu NVARCHAR(255),
        -- Nhan tai lieu
        MaDoiTuong NVARCHAR(MAX),
        -- Danh sach ma doi tuong dang JSON string
        TenSanPham NVARCHAR(500),
        -- Ten san pham / Tieu de
        CongDoan NVARCHAR(255),
        -- To san xuat / Quy trinh
        VatLieu NVARCHAR(255),
        -- Vat lieu
        SoLuong INT,
        -- So luong
        NguoiLap NVARCHAR(255),
        -- Noi rong (cu NVARCHAR(100)) tranh truncate
        NgayVe DATE,
        -- Ngay phat hanh / Ngay ve
        DungSaiDay NVARCHAR(255),
        -- Noi rong (cu NVARCHAR(100))
        DungSaiKhac NVARCHAR(255),
        -- Noi rong (cu NVARCHAR(100))
        KichThuocTongThe NVARCHAR(255),
        -- Noi rong (cu NVARCHAR(100))
        HDCV NVARCHAR(MAX),
        -- Huong dan cong viec
        YCKT NVARCHAR(MAX),
        -- Yeu cau ky thuat
        CONSTRAINT FK_TaiLieuKyThuat_TaiLieu FOREIGN KEY (DocID) REFERENCES TaiLieu(DocID) ON DELETE CASCADE,
        -- Bao toan ven du lieu: moi (DocID, TrangSo) chi 1 dong metadata.
        CONSTRAINT UQ_TaiLieuKyThuat_Doc_Trang UNIQUE (DocID, TrangSo)
    );
GO
    CREATE TABLE BangKeVatTu (
        ID INT IDENTITY(1, 1) PRIMARY KEY,
        DocID INT NOT NULL,
        TrangSo INT,
        MaHang NVARCHAR(255),
        TenVatTu NVARCHAR(500),
        VatLieu NVARCHAR(255),
        SoLuong INT,
        GhiChu NVARCHAR(MAX),
        Unit NVARCHAR(50) NULL,
        Confidence FLOAT NULL,
        RawRowJson NVARCHAR(MAX) NULL,
        SourceTableIndex INT NULL,
        CONSTRAINT FK_BangKeVatTu_TaiLieu FOREIGN KEY (DocID) REFERENCES TaiLieu(DocID) ON DELETE CASCADE
    );
GO
    CREATE INDEX IX_BangKeVatTu_DocID ON BangKeVatTu(DocID);
    CREATE INDEX IX_BangKeVatTu_MaHang ON BangKeVatTu(MaHang);
    CREATE INDEX IX_BangKeVatTu_VatLieu ON BangKeVatTu(VatLieu);
GO -- ==========================================
    -- PHAN 3: LUU TRU LICH SU CHAT
    -- ==========================================
    CREATE TABLE LichSuChat (
        ChatID INT IDENTITY(1, 1) PRIMARY KEY,
        SessionID VARCHAR(100) NOT NULL,
        CauHoi_User NVARCHAR(MAX) NOT NULL,
        TraLoi_Bot NVARCHAR(MAX) NOT NULL,
        HinhAnhUpload NVARCHAR(500),
        -- Duong dan anh user upload (neu co)
        RefImages NVARCHAR(MAX),
        -- FIX C5: danh sach duong dan ban ve can cu dang JSON
        DanhGia TINYINT,
        -- 1: Like, -1: Dislike, NULL: Chua danh gia
        ThoiGian DATETIME DEFAULT GETDATE()
    );
GO 

    CREATE TABLE FeedbackReview (
        FeedbackID INT IDENTITY(1, 1) PRIMARY KEY,
        ChatID INT NOT NULL,
        Question NVARCHAR(MAX),
        BotAnswer NVARCHAR(MAX),
        FailureType NVARCHAR(100),
        CorrectAnswer NVARCHAR(MAX),
        CorrectSourceDocID INT NULL,
        ReviewerNote NVARCHAR(MAX),
        AddedToGoldenSet BIT DEFAULT 0,
        CreatedAt DATETIME DEFAULT GETDATE(),
        CONSTRAINT FK_FeedbackReview_ChatID FOREIGN KEY (ChatID) REFERENCES LichSuChat(ChatID) ON DELETE CASCADE
    );
GO

-- Index composite: vua loc theo Session, vua sap xep theo thoi gian
    CREATE NONCLUSTERED INDEX IX_LichSuChat_Session_Time ON LichSuChat(SessionID, ThoiGian);
GO -- ==========================================
    -- MIGRATION (CHI DUNG KHI DB DA TON TAI VA KHONG MUON XOA DU LIEU CU)
    -- Thay vi chay lai toan bo script (se xoa het bang), chay rieng doan duoi:
    -- ==========================================
    -- IF NOT EXISTS (
    --     SELECT 1 FROM sys.columns
    --     WHERE Name = N'RefImages' AND Object_ID = Object_ID(N'dbo.LichSuChat')
    -- )
    -- BEGIN
    --     ALTER TABLE dbo.LichSuChat ADD RefImages NVARCHAR(MAX) NULL;
    -- END
    -- GO

-- ==========================================
-- PHAN 4: AUTH & ROLES (Phase 5)
-- ==========================================
CREATE TABLE Users (
    UserID INT IDENTITY(1,1) PRIMARY KEY,
    Username NVARCHAR(255) UNIQUE NOT NULL,
    PasswordHash NVARCHAR(500) NOT NULL,
    DisplayName NVARCHAR(255),
    Department NVARCHAR(255),
    IsActive BIT DEFAULT 1,
    CreatedAt DATETIME DEFAULT GETDATE()
);
GO

CREATE TABLE Roles (
    RoleID INT IDENTITY(1,1) PRIMARY KEY,
    RoleName NVARCHAR(100) UNIQUE NOT NULL
);
GO

CREATE TABLE UserRoles (
    UserID INT,
    RoleID INT,
    PRIMARY KEY (UserID, RoleID)
);
GO

-- Seed data for Auth
INSERT INTO Roles (RoleName) VALUES ('admin'), ('reviewer'), ('uploader'), ('viewer');
INSERT INTO Users (Username, PasswordHash, DisplayName, Department) VALUES ('admin', '$2b$12$GjF79FWNuuNfl4VWOA28iOk4ubZWWd5OltSsAiZ5TgaWPz5UtAZpu', 'Administrator', 'IT');
INSERT INTO UserRoles (UserID, RoleID) VALUES (1, 1);
INSERT INTO Users (Username, PasswordHash, DisplayName, Department) VALUES ('viewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Nhan Vien A', 'Tu_Hoc');
INSERT INTO UserRoles (UserID, RoleID) VALUES (2, 4);
INSERT INTO Users (Username, PasswordHash, DisplayName, Department) VALUES ('uploader1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Uploader', 'Ky_Thuat');
INSERT INTO UserRoles (UserID, RoleID) VALUES (3, 3);
INSERT INTO Users (Username, PasswordHash, DisplayName, Department) VALUES ('reviewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Truong Phong', 'Ky_Thuat');
INSERT INTO UserRoles (UserID, RoleID) VALUES (4, 2);
GO

-- ==========================================
-- PHAN 5: FEEDBACK REVIEW (Phase 6)
-- ==========================================
CREATE TABLE FeedbackReview (
    FeedbackID INT IDENTITY(1,1) PRIMARY KEY,
    ChatID INT,
    Question NVARCHAR(MAX),
    BotAnswer NVARCHAR(MAX),
    FailureType NVARCHAR(100),
    CorrectAnswer NVARCHAR(MAX),
    CorrectSourceDocID INT NULL,
    ReviewerNote NVARCHAR(MAX),
    AddedToGoldenSet BIT DEFAULT 0,
    CreatedAt DATETIME DEFAULT GETDATE()
);
GO