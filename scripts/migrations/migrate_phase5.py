from sqlalchemy import text
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db_logic import engine

def migrate_phase5():
    with engine.begin() as conn:
        conn.execute(text("""
        IF OBJECT_ID('dbo.Users', 'U') IS NULL
        BEGIN
            CREATE TABLE Users (
                UserID INT IDENTITY(1,1) PRIMARY KEY,
                Username NVARCHAR(255) UNIQUE NOT NULL,
                PasswordHash NVARCHAR(500) NOT NULL,
                DisplayName NVARCHAR(255),
                Department NVARCHAR(255),
                IsActive BIT DEFAULT 1,
                CreatedAt DATETIME DEFAULT GETDATE()
            );
        END
        """))

        conn.execute(text("""
        IF OBJECT_ID('dbo.Roles', 'U') IS NULL
        BEGIN
            CREATE TABLE Roles (
                RoleID INT IDENTITY(1,1) PRIMARY KEY,
                RoleName NVARCHAR(100) UNIQUE NOT NULL
            );
        END
        """))

        conn.execute(text("""
        IF OBJECT_ID('dbo.UserRoles', 'U') IS NULL
        BEGIN
            CREATE TABLE UserRoles (
                UserID INT,
                RoleID INT,
                PRIMARY KEY (UserID, RoleID)
            );
        END
        """))

        conn.execute(text("""
        IF OBJECT_ID('dbo.UserDepartments', 'U') IS NULL
        BEGIN
            CREATE TABLE UserDepartments (
                UserID INT NOT NULL,
                Department NVARCHAR(255) NOT NULL,
                PRIMARY KEY (UserID, Department)
            );
        END
        """))

        conn.execute(text("""
        IF OBJECT_ID('dbo.AuditLog', 'U') IS NULL
        BEGIN
            CREATE TABLE AuditLog (
                AuditID INT IDENTITY(1,1) PRIMARY KEY,
                UserID INT NULL,
                Username NVARCHAR(255),
                Action NVARCHAR(100) NOT NULL,
                EntityType NVARCHAR(100),
                EntityID INT NULL,
                Details NVARCHAR(MAX),
                CreatedAt DATETIME DEFAULT GETDATE()
            );
        END
        """))

        # Roles idempotent
        for role in ["admin", "reviewer", "uploader", "viewer"]:
            conn.execute(text("""
            IF NOT EXISTS (SELECT 1 FROM Roles WHERE RoleName = :role)
            BEGIN
                INSERT INTO Roles (RoleName) VALUES (:role)
            END
            """), {"role": role})

        # Seed admin nếu chưa có
        conn.execute(text("""
        IF NOT EXISTS (SELECT 1 FROM Users WHERE Username = 'admin')
        BEGIN
            INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
            VALUES (
                'admin',
                '$2b$12$GjF79FWNuuNfl4VWOA28iOk4ubZWWd5OltSsAiZ5TgaWPz5UtAZpu',
                'Administrator',
                'IT'
            )
        END
        """))

        # Gán role admin theo lookup ID, không giả định UserID=1
        conn.execute(text("""
        INSERT INTO UserRoles (UserID, RoleID)
        SELECT u.UserID, r.RoleID
        FROM Users u
        JOIN Roles r ON r.RoleName = 'admin'
        WHERE u.Username = 'admin'
          AND NOT EXISTS (
              SELECT 1 FROM UserRoles ur
              WHERE ur.UserID = u.UserID AND ur.RoleID = r.RoleID
          )
        """))

        # Seed additional test users
        seed_users = [
            ("viewer1", "$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2", "Nhan Vien A", "Tu_Hoc", "viewer"),
            ("uploader1", "$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2", "Uploader", "Ky_Thuat", "uploader"),
            ("reviewer1", "$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2", "Truong Phong", "Ky_Thuat", "reviewer"),
        ]

        for username, password_hash, display_name, department, role in seed_users:
            conn.execute(text("""
            IF NOT EXISTS (SELECT 1 FROM Users WHERE Username = :username)
            BEGIN
                INSERT INTO Users (Username, PasswordHash, DisplayName, Department)
                VALUES (:username, :password_hash, :display_name, :department)
            END
            """), {
                "username": username,
                "password_hash": password_hash,
                "display_name": display_name,
                "department": department,
            })

            conn.execute(text("""
            INSERT INTO UserRoles (UserID, RoleID)
            SELECT u.UserID, r.RoleID
            FROM Users u
            JOIN Roles r ON r.RoleName = :role
            WHERE u.Username = :username
              AND NOT EXISTS (
                  SELECT 1 FROM UserRoles ur
                  WHERE ur.UserID = u.UserID AND ur.RoleID = r.RoleID
              )
            """), {
                "username": username,
                "role": role,
            })

        # Gán department theo lookup ID
        conn.execute(text("""
        INSERT INTO UserDepartments (UserID, Department)
        SELECT u.UserID, u.Department
        FROM Users u
        WHERE u.Department IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM UserDepartments ud
              WHERE ud.UserID = u.UserID AND ud.Department = u.Department
          )
        """))

    print("Phase 5 migration completed.")

if __name__ == "__main__":
    migrate_phase5()
