// ROM-free compiler prerequisite check; no emulator state or transport used.
#include <functional>
#include <memory>
#include <utility>

struct Listener
{
    const std::function<void()> callback;
    bool pending{};
};

int main()
{
    bool called = false;
    std::function<void()> callback = [&called] { called = true; };
    auto listener = std::make_unique<Listener>(std::move(callback));
    listener->callback();
    return called && !listener->pending ? 0 : 1;
}
