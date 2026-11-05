import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Button, EmptyState } from "../components/ui";

export default function NotFound() {
  return (
    <div className="card mx-auto max-w-xl">
      <EmptyState
        icon={<Compass className="h-6 w-6" />}
        title="Page not found"
        description="This route is not part of the NAT64 ISP Manager control plane. Use the navigation to return to a supported page."
        action={
          <Link to="/">
            <Button variant="primary">Go to dashboard</Button>
          </Link>
        }
      />
    </div>
  );
}
