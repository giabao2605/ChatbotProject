from sqlalchemy import text
import sys
import os

# Add parent dir to path so we can import db_logic
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_logic import engine

def migrate_phase5():
    queries = [
        """
        CREATE TABLE Users (
            UserID INT IDENTITY(1,1) PRIMARY KEY,
            Username NVARCHAR(255) UNIQUE NOT NULL,
            PasswordHash NVARCHAR(500) NOT NULL,
            DisplayName NVARCHAR(255),
            Department NVARCHAR(255),
            IsActive BIT DEFAULT 1,
            CreatedAt DATETIME DEFAULT GETDATE()
        );
        """,
        """
        CREATE TABLE Roles (
            RoleID INT IDENTITY(1,1) PRIMARY KEY,
            RoleName NVARCHAR(100) UNIQUE NOT NULL
        );
        """,
        """
        CREATE TABLE UserRoles (
            UserID INT,
            RoleID INT,
            PRIMARY KEY (UserID, RoleID)
        );
        """,
        "INSERT INTO Roles (RoleName) VALUES ('admin'), ('reviewer'), ('uploader'), ('viewer');",
        # Default admin user with hash of "admin123" (fake hash for now or simple clear text for demo)
        # Using simple md5 or just clear text for this demo? Let's use simple hash or just plain text for now, but the prompt says PasswordHash.
        "INSERT INTO Users (Username, PasswordHash, DisplayName, Department) VALUES ('admin', '$2b$12$GjF79FWNuuNfl4VWOA28iOk4ubZWWd5OltSsAiZ5TgaWPz5UtAZpu', 'Administrator', 'IT');",
        "INSERT INTO UserRoles (UserID, RoleID) VALUES (1, 1);",
        # Default user 1 (viewer)
        "INSERT INTO Users (Username, PasswordHash, DisplayName, Department) VALUES ('viewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Nhan Vien A', 'Tu_Hoc');",
        "INSERT INTO UserRoles (UserID, RoleID) VALUES (2, 4);",
        # Default uploader
        "INSERT INTO Users (Username, PasswordHash, DisplayName, Department) VALUES ('uploader1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Uploader', 'Ky_Thuat');",
        "INSERT INTO UserRoles (UserID, RoleID) VALUES (3, 3);",
        # Default reviewer
        "INSERT INTO Users (Username, PasswordHash, DisplayName, Department) VALUES ('reviewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Truong Phong', 'Ky_Thuat');",
        "INSERT INTO UserRoles (UserID, RoleID) VALUES (4, 2);"
    ]
    with engine.begin() as conn:
        for q in queries:
            try:
                conn.execute(text(q))
                print(f"Executed OK.")
            except Exception as e:
                print(f"Skipped (probably exists): Error: {e}")

if __name__ == '__main__':
    migrate_phase5()
