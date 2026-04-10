from pathlib import Path
import json

OUTPUT_DIR = Path(__file__).parent / "output" / "resumes"

MAPPING = {
    "SaiKumar_Resume_1.pdf": "Stripe_Software_Engineer__Core_Techno_20260405_190805.pdf",
    "SaiKumar_Resume_2.pdf": "Stripe_Software_Engineer__Data___AI_20260405_190811.pdf",
    "SaiKumar_Resume_3.pdf": "Stripe_Software_Engineer__Fee_Insight_20260405_190819.pdf",
    "SaiKumar_Resume_4.pdf": "Uber_Sr_Software_Engineer_20260407_190828.pdf",
    "SaiKumar_Resume_5.pdf": "JPMC_Software_Engineer_II_20260407_190837.pdf",
    "SaiKumar_Resume_6.pdf": "JPMC_Software_Engineer_III___Java_F_20260407_190846.pdf",
    "SaiKumar_Resume_7.pdf": "Microsoft_Software_Engineer_II_20260407_190857.pdf",
    "SaiKumar_Resume_8.pdf": "Microsoft_Software_Engineer_II_20260407_190909.pdf",
    "SaiKumar_Resume_9.pdf": "NVIDIA_Systems_Software_Engineer__AI__20260407_190919.pdf",
    "SaiKumar_Resume_10.pdf": "Intuit_Senior_Software_Engineer___Cre_20260407_190939.pdf",
    "SaiKumar_Resume_11.pdf": "Microsoft_Software_Engineer_II_20260407_190950.pdf",
    "SaiKumar_Resume_12.pdf": "JPMC_Software_Engineer_III___Java_A_20260408_190959.pdf",
    "SaiKumar_Resume_13.pdf": "JPMC_Software_Engineer_II___Java_Fu_20260408_191010.pdf",
    "SaiKumar_Resume_14.pdf": "JPMC_Software_Engineer_III__Java__K_20260408_191019.pdf",
    "SaiKumar_Resume_15.pdf": "JPMC_Software_Engineer_III_20260408_191028.pdf",
    "SaiKumar_Resume_16.pdf": "Microsoft_Senior_Software_Engineer_20260408_191039.pdf",
    "SaiKumar_Resume_17.pdf": "Cloudflare_Senior_Software_Engineer___Bac_20260408_191045.pdf",
    "SaiKumar_Resume_18.pdf": "Cloudflare_Senior_Software_Engineer__Full_20260408_191051.pdf",
    "SaiKumar_Resume_19.pdf": "Cloudflare_Software_Engineer__Backend__20260408_191058.pdf",
    "SaiKumar_Resume_20.pdf": "Cloudflare_Software_Engineer___Platforms__20260408_191105.pdf",
    "SaiKumar_Resume_21.pdf": "Stripe_Software_Engineer__Core_Techno_20260405_7618977.pdf",
    "SaiKumar_Resume_22.pdf": "Stripe_Software_Engineer__Data___AI_20260405_7529428.pdf",
    "SaiKumar_Resume_23.pdf": "Stripe_Software_Engineer__Fee_Insight_20260405_7436194.pdf",
    "SaiKumar_Resume_24.pdf": "Uber_Sr_Software_Engineer_20260407_158063.pdf",
    "SaiKumar_Resume_25.pdf": "JPMC_Software_Engineer_II_20260407_210711765.pdf",
    "SaiKumar_Resume_26.pdf": "JPMC_Software_Engineer_III___Java_F_20260407_210728800.pdf",
    "SaiKumar_Resume_27.pdf": "Microsoft_Software_Engineer_II_20260407_1970393556856717.pdf",
    "SaiKumar_Resume_28.pdf": "Microsoft_Software_Engineer_II_20260407_1970393556834957.pdf",
    "SaiKumar_Resume_29.pdf": "NVIDIA_Systems_Software_Engineer__AI__20260407_893393529328.pdf",
    "SaiKumar_Resume_30.pdf": "Microsoft_Software_Engineer_II_20260407_1970393556856725.pdf",
    "SaiKumar_Resume_31.pdf": "JPMC_Software_Engineer_III___Java_A_20260408_210711202.pdf",
    "SaiKumar_Resume_32.pdf": "JPMC_Software_Engineer_II___Java_Fu_20260408_210727572.pdf",
    "SaiKumar_Resume_33.pdf": "JPMC_Software_Engineer_III__Java__K_20260408_210730804.pdf",
    "SaiKumar_Resume_34.pdf": "JPMC_Software_Engineer_III_20260408_210725978.pdf",
    "SaiKumar_Resume_35.pdf": "Microsoft_Senior_Software_Engineer_20260408_1970393556752488.pdf",
    "SaiKumar_Resume_36.pdf": "Cloudflare_Senior_Software_Engineer___Bac_20260408_7244991.pdf",
    "SaiKumar_Resume_37.pdf": "Cloudflare_Senior_Software_Engineer__Full_20260408_7566807.pdf",
    "SaiKumar_Resume_38.pdf": "Cloudflare_Software_Engineer__Backend__20260408_7603643.pdf",
    "SaiKumar_Resume_39.pdf": "Cloudflare_Software_Engineer___Platforms__20260408_6972536.pdf",
    "SaiKumar_Resume_40.pdf": "Uber_Software_Engineer_II___Data_20260409_157584.pdf",
    "SaiKumar_Resume_41.pdf": "Apple_Site_Reliability_Engineer_20260409_200656049_1052.pdf",
    "SaiKumar_Resume_42.pdf": "JPMC_Software_Engineer_III___Java___20260409_210729026.pdf",
    "SaiKumar_Resume_43.pdf": "JPMC_Software_Engineer_II_20260409_210732846.pdf",
    "SaiKumar_Resume_44.pdf": "JPMC_Software_Engineer_II_20260409_210732848.pdf",
    "SaiKumar_Resume_45.pdf": "Microsoft_Software_Engineer_II_20260409_1970393556752049.pdf",
    "SaiKumar_Resume_46.pdf": "Uber_Software_Engineer_II_20260410_156855.pdf",
    "SaiKumar_Resume_47.pdf": "JPMC_Site_Reliability_Engineer_III_20260410_210717190.pdf",
    "SaiKumar_Resume_48.pdf": "JPMC_Software_Engineer_III_20260410_210728137.pdf",
    "SaiKumar_Resume_49.pdf": "JPMC_Software_Engineer_II_20260410_210732838.pdf",
    "SaiKumar_Resume_50.pdf": "JPMC_Software_Engineer_II_20260410_210732844.pdf",
}

ok = 0
for new_name, old_name in MAPPING.items():
    src = OUTPUT_DIR / new_name
    dst = OUTPUT_DIR / old_name
    if src.exists():
        src.rename(dst)
        print(f"  {new_name}  →  {old_name}")
        ok += 1
    else:
        print(f"  [MISSING] {new_name}")

# Remove manifest so the new dedup system starts clean
manifest = OUTPUT_DIR / "manifest.json"
if manifest.exists():
    manifest.unlink()
    print("\nDeleted manifest.json")

print(f"\nReverted {ok}/50 file(s).")
