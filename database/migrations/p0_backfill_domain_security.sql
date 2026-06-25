-- =============================================================================
-- P0 MIGRATION: Backfill Domain + SecurityLevel cho du lieu cu, va Clearance
-- Idempotent: chay lai nhieu lan deu an toan.
-- Chay TREN DATABASE HIEN CO (sau khi da apply schema init moi nhat).
-- =============================================================================
SET NOCOUNT ON;
GO

-- 1) Backfill Domain cho TaiLieu dua tren ThuMuc (khop voi domain_registry.py)
UPDATE TaiLieu SET Domain = 'co_khi'
 WHERE (Domain IS NULL OR Domain = '')
   AND ThuMuc IN ('To_Han','To_Dap','To_Son','To_Nham','To_Phoi','To_Tien_Phay',
                  'To_Dong_Goi','To_Ban_Le','Bang_Ke','Gia_Cong_Ngoai');
GO
UPDATE TaiLieu SET Domain = 'ky_thuat'
 WHERE (Domain IS NULL OR Domain = '') AND ThuMuc = 'Ky_Thuat';
GO

-- Domain phi co khi -> mac dinh confidential (ke toan, nhan su)
UPDATE TaiLieu SET Domain = 'ke_toan', SecurityLevel = 'confidential'
 WHERE ThuMuc = 'Ke_Toan';
GO
UPDATE TaiLieu SET Domain = 'nhan_su', SecurityLevel = 'confidential'
 WHERE ThuMuc = 'Nhan_Su';
GO

-- Domain chung -> public
UPDATE TaiLieu SET Domain = 'chung', SecurityLevel = 'public'
 WHERE (Domain IS NULL OR Domain = '') AND ThuMuc IN ('CHUNG','Tu_Hoc','IT');
GO

-- Fallback: con NULL thi gan 'chung' / 'internal'
UPDATE TaiLieu SET Domain = 'chung' WHERE Domain IS NULL OR Domain = '';
GO
UPDATE TaiLieu SET SecurityLevel = 'internal' WHERE SecurityLevel IS NULL OR SecurityLevel = '';
GO

-- 2) Dam bao moi user deu co ban ghi clearance (mac dinh 'internal')
INSERT INTO UserSecurityClearance (UserID, MaxLevel)
SELECT u.UserID, 'internal'
  FROM Users u
 WHERE NOT EXISTS (SELECT 1 FROM UserSecurityClearance c WHERE c.UserID = u.UserID);
GO

-- 3) Nang clearance cho cac role can xem tai lieu mat (tuy chinh theo nhu cau)
--    Vi du: nguoi thuoc phong Ke toan / Nhan su can xem confidential.
--    >>> Sua danh sach Username ben duoi cho dung thuc te cua ban <<<
/*
UPDATE c SET MaxLevel = 'confidential'
  FROM UserSecurityClearance c
  JOIN Users u ON u.UserID = c.UserID
 WHERE u.Username IN ('ketoan_truong', 'hr_manager');
GO
*/

PRINT 'P0 backfill domain/security hoan tat.';
GO
