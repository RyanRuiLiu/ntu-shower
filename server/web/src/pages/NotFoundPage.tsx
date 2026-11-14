import { Button } from "@heroui/react";
import { useNavigate } from "react-router";

export function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4">
      <div className="text-4xl">404</div>
      <p className="text-default-500">页面不存在</p>
      <Button variant="ghost" onPress={() => navigate("/")}>
        回概览
      </Button>
    </div>
  );
}
